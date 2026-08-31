import React from "react";
import { Search, Plus, Loader2, MessageSquare } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLanguage } from "@/i18n/LanguageContext";
import { api } from "@/services/api";
import type { Conversation } from "@/types/api";

interface ConversationSearchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 选中某条对话（传 conversation id 字符串，与 ChatSidebar onSelectChat 一致） */
  onSelectChat: (chatId: string) => void;
  /** 新建对话 */
  onNewTask: () => void;
}

interface DateGroup {
  key: string;
  label: string;
  items: Conversation[];
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** 把会话按 last_active_at（回退 created_at）分到 前7天 / 前30天 / 按月 三类，保持输入顺序 */
function groupConversationsByDate(
  conversations: Conversation[],
  language: string,
  t: (k: any) => string,
): DateGroup[] {
  const now = Date.now();
  const order: string[] = [];
  const map: Record<string, DateGroup> = {};

  const monthLabel = (d: Date): string => {
    const sameYear = d.getFullYear() === new Date().getFullYear();
    if (language === "zh") {
      return sameYear ? `${d.getMonth() + 1}月` : `${d.getFullYear()}年${d.getMonth() + 1}月`;
    }
    const month = d.toLocaleString("en-US", { month: "long" });
    return sameYear ? month : `${month} ${d.getFullYear()}`;
  };

  for (const conv of conversations) {
    const raw = conv.last_active_at || conv.created_at || conv.updated_at;
    const d = raw ? new Date(raw) : null;
    let key: string;
    let label: string;
    if (!d || isNaN(d.getTime())) {
      key = "older";
      label = t("earlier") || "Earlier";
    } else {
      const days = (now - d.getTime()) / DAY_MS;
      if (days < 7) {
        key = "last7";
        label = t("last7Days") || "Previous 7 days";
      } else if (days < 30) {
        key = "last30";
        label = t("last30Days") || "Previous 30 days";
      } else {
        key = `m-${d.getFullYear()}-${d.getMonth()}`;
        label = monthLabel(d);
      }
    }
    if (!map[key]) {
      map[key] = { key, label, items: [] };
      order.push(key);
    }
    map[key].items.push(conv);
  }

  return order.map((k) => map[k]);
}

// 与左侧栏 ChatSidebar 的标题推导保持一致（CreateVideoPage.getConversationDisplayTitle）：
// 优先「首条用户输入/preview 的第一句」，再退回 conv.title。
function getFirstSentence(raw: unknown): string {
  const text = String(raw ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const firstLine = text.split("\n")[0]?.trim() || "";
  if (!firstLine) return "";
  const sentenceMatch = firstLine.match(/^(.+?[。！？.!?])(?:\s|$)/);
  if (sentenceMatch?.[1]) {
    return sentenceMatch[1].trim();
  }
  return firstLine.length > 80 ? `${firstLine.slice(0, 80).trim()}...` : firstLine;
}

function getConvDisplayTitle(conv: Conversation, fallback: string): string {
  const firstSentence =
    getFirstSentence((conv as any)?.first_user_input) ||
    getFirstSentence((conv as any)?.user_input) ||
    getFirstSentence(conv?.preview);
  return firstSentence || conv?.title || fallback;
}

export const ConversationSearchDialog = ({
  open,
  onOpenChange,
  onSelectChat,
  onNewTask,
}: ConversationSearchDialogProps) => {
  const { language, t } = useLanguage();
  const [query, setQuery] = React.useState("");
  const [results, setResults] = React.useState<Conversation[]>([]);
  const [loading, setLoading] = React.useState(false);
  const reqIdRef = React.useRef(0);

  // 打开时重置查询并加载最近对话
  React.useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
      setLoading(false);
    }
  }, [open]);

  // 防抖检索：query 变化 300ms 后调后端（空 query 返回最近对话）
  React.useEffect(() => {
    if (!open) return;
    const reqId = ++reqIdRef.current;
    setLoading(true);
    const handle = setTimeout(async () => {
      try {
        const trimmed = query.trim();
        const res = await api.conversation.getConversations(1, 30, trimmed ? { q: trimmed } : undefined);
        if (reqId !== reqIdRef.current) return; // 丢弃过期请求
        setResults(res.code === 0 && res.data ? res.data.conversations || [] : []);
      } catch {
        if (reqId === reqIdRef.current) setResults([]);
      } finally {
        if (reqId === reqIdRef.current) setLoading(false);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [query, open]);

  const groups = React.useMemo(
    () => groupConversationsByDate(results, language, t),
    [results, language, t],
  );

  const handleSelect = (conv: Conversation) => {
    onSelectChat(String(conv.id));
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="overflow-hidden p-0 gap-0 sm:max-w-[640px] top-[15%] translate-y-0">
        {/* 搜索框 */}
        <div className="flex items-center border-b px-4">
          <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchChats") || "Search chats..."}
            className="flex h-12 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground"
          />
          {loading && <Loader2 className="ml-2 h-4 w-4 shrink-0 animate-spin opacity-50" />}
        </div>

        <ScrollArea className="max-h-[60vh]">
          <div className="p-2">
            {/* 新聊天 */}
            <button
              type="button"
              onClick={() => {
                onNewTask();
                onOpenChange(false);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-3 text-sm hover:bg-accent hover:text-accent-foreground"
            >
              <Plus className="h-4 w-4 shrink-0" />
              <span>{t("newTask") || "New Chat"}</span>
            </button>

            {!loading && results.length === 0 && (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {query.trim()
                  ? t("noSearchResults") || "No matching chats"
                  : t("noConversations") || "No conversations yet"}
              </div>
            )}

            {groups.map((group) => (
              <div key={group.key} className="mt-2">
                <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">{group.label}</div>
                {group.items.map((conv) => (
                  <button
                    type="button"
                    key={conv.id}
                    onClick={() => handleSelect(conv)}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-2.5 text-left text-sm hover:bg-accent hover:text-accent-foreground"
                  >
                    <MessageSquare className="h-4 w-4 shrink-0 opacity-60" />
                    <span className="min-w-0 flex-1 truncate">
                      {getConvDisplayTitle(conv, t("untitledConversation") || "Untitled")}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
};

export default ConversationSearchDialog;
