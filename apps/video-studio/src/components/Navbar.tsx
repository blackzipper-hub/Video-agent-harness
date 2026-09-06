import { useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { useLanguage } from "@/i18n/LanguageContext";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Coins,
  Globe,
  Languages,
  LogOut,
  HelpCircle,
  Users,
  ArrowUp,
  User as UserIcon,
  LayoutDashboard,
  Menu,
  CreditCard,
} from "lucide-react";
import defaultAvatar from "@/assets/user-avatar-capybara.png";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useSidebar } from "@/components/ui/sidebar";
import { toast } from "sonner";

interface NavbarProps {
  showSidebarTrigger?: boolean;
  hideBrand?: boolean;
}

const Navbar = ({ showSidebarTrigger = false, hideBrand = false }: NavbarProps) => {
  const navigate = useNavigate();
  const { isLoggedIn, user, logout, isLoading: authLoading } = useAuth();
  const { t } = useLanguage();
  const sidebar = showSidebarTrigger ? useSidebar() : null;

  const handleSignOut = async () => {
    try {
      await logout();
      toast.success(t('logoutSuccess'));
      navigate("/auth");
    } catch (error: unknown) {
      toast.error(t('logoutFailedRetry'));
    }
  };

  const getUserInitials = () => {
    if (!user?.email) return "U";
    return user.email.charAt(0).toUpperCase();
  };

  const getUserName = () => {
    if (user?.email) {
      return user.email.split('@')[0];
    }
    return 'User';
  };

  const getUserAccount = () => {
    return user?.email || user?.phone || '';
  };

  return (
    <TooltipProvider>
      <header className="border-b border-border/30 bg-background/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="container mx-auto px-4 sm:px-6 py-3 sm:py-4">
          <div className="flex items-center justify-between">
            {/* Left: Logo and Navigation */}
            <div className="flex items-center gap-2 sm:gap-4">
              {showSidebarTrigger && sidebar && isLoggedIn && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="md:hidden w-8 h-8"
                  onClick={() => sidebar.setOpenMobile(true)}
                >
                  <Menu className="w-5 h-5" />
                </Button>
              )}

              {!hideBrand && (
                <button onClick={() => navigate("/")} className="flex items-center gap-1.5 sm:gap-2 hover:opacity-80 transition-opacity">
                  <img src={`${import.meta.env.BASE_URL}logo-internal.png`} alt="Cuti" className="w-6 h-6 sm:w-8 sm:h-8" />
                  <span className="font-righteous text-base sm:text-lg font-bold gradient-text">Cuti</span>
                </button>
              )}
            </div>

            {/* Right: User Info */}
            <div className="flex items-center gap-2 sm:gap-4">
              {/* 鉴权未完成时显示加载，避免误显示「登录」 */}
              {authLoading ? (
                <div className="w-8 h-8 rounded-full border-2 border-primary/30 border-t-primary animate-spin" />
              ) : null}
              {/* Credits Display */}
              {!authLoading && isLoggedIn && user && (
                <Tooltip>
                  <DropdownMenu>
                    <TooltipTrigger asChild>
                      <DropdownMenuTrigger asChild>
                        <Button 
                          variant="ghost" 
                          className="h-8 sm:h-10 px-2 sm:px-3 gap-1.5 sm:gap-2"
                        >
                          <Coins className="w-4 h-4 sm:w-5 sm:h-5" strokeWidth={1.5} />
                          <span className="text-sm sm:text-base font-medium">
                            {user.credits || 0}
                          </span>
                        </Button>
                      </DropdownMenuTrigger>
                    </TooltipTrigger>
                    <DropdownMenuContent className="w-56" align="end">
                      <DropdownMenuItem className="cursor-pointer" onClick={() => navigate("/pricing")}>
                        <ArrowUp className="mr-2 h-4 w-4" />
                        <span>{t('upgrade')}</span>
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <TooltipContent>
                    <p>{t('credits')}</p>
                  </TooltipContent>
                </Tooltip>
              )}

              {/* User Dropdown */}
              {!authLoading && isLoggedIn ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="p-0 rounded-full">
                    <Avatar className="w-7 h-7 sm:w-8 sm:h-8">
                      <img src={defaultAvatar} alt={t('userAvatar')} className="w-full h-full object-cover" />
                      <AvatarFallback className="bg-primary text-primary-foreground text-sm">
                        {getUserInitials()}
                      </AvatarFallback>
                    </Avatar>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56" align="end">
                  <div className="px-2 py-2">
                    <div className="flex items-center gap-3">
                      <Avatar className="w-10 h-10 flex-shrink-0">
                      <img src={defaultAvatar} alt={t('userAvatar')} className="w-full h-full object-cover" />
                        <AvatarFallback className="bg-primary text-primary-foreground text-sm">
                          {getUserInitials()}
                        </AvatarFallback>
                      </Avatar>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium font-inter truncate">
                          {getUserName()}
                        </p>
                        <p className="text-xs text-muted-foreground truncate">
                          {getUserAccount()}
                        </p>
                      </div>
                    </div>
                  </div>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="cursor-pointer" onClick={() => navigate("/pricing")}>
                    <Coins className="mr-2 h-4 w-4" />
                    <span>{t('plansAndPricing')}</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem className="cursor-pointer" onClick={() => navigate("/subscription")}>
                    <CreditCard className="mr-2 h-4 w-4" />
                    <span>{t('mySubscription')}</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem className="cursor-pointer">
                    <Users className="mr-2 h-4 w-4" />
                    <span>{t('community')}</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem className="cursor-pointer">
                    <HelpCircle className="mr-2 h-4 w-4" />
                    <span>{t('contactUs')}</span>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="cursor-pointer" onClick={handleSignOut}>
                    <LogOut className="mr-2 h-4 w-4" />
                    <span>{t('logOut')}</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              ) : !authLoading ? (
                <Button variant="ghost" size="icon" className="p-0 rounded-full" onClick={() => navigate("/auth")}>
                  <Avatar className="w-7 h-7 sm:w-8 sm:h-8">
                    <img src={defaultAvatar} alt={t('userAvatar')} className="w-full h-full object-cover" />
                    <AvatarFallback className="bg-muted text-muted-foreground">
                      <UserIcon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                    </AvatarFallback>
                  </Avatar>
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      </header>
    </TooltipProvider>
  );
};

export default Navbar;
