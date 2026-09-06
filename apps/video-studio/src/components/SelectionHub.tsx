import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { Image as ImageIcon } from 'lucide-react'
import { useLanguage } from '@/i18n/LanguageContext'
import demoCharacterLipsync from '@/assets/demo-character-lipsync-kitty.png'
import demoStorytellingImage from '@/assets/demo-storytelling-man-dog.png'
import demoProductLaunchImage from '@/assets/demo-product-launch.webp'
import lipsyncCardBg from '@/assets/lipsync-music-video-bg.png'
import lipsyncDemoAudioUrl from '@/assets/audio/make-your-music-video-example.mp3?url'
import storytellingCardBg from '@/assets/storytelling-music-video-bg.png'
import HeroSection from '@/components/HeroSection'
import GenerationBox from '@/components/GenerationBox'
import { Avatar, AvatarImage, AvatarFallback } from '@/components/ui/avatar'
import exploreChatRobot from '@/assets/explore_chat_robot.png'
import Sidebar from '@/components/Sidebar'
import MobileBottomNav from '@/components/MobileBottomNav'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { NEW_YEAR_AVATAR_DISPLAY_PROMPT } from '@/utils/promptMapping'

const SelectionHub = () => {
  const { t, language } = useLanguage()

  const [externalPrompt, setExternalPrompt] = useState<string>('')
  const [promptKey, setPromptKey] = useState(0)
  const [externalFiles, setExternalFiles] = useState<File[]>([])
  /** 内容模版：默认不传（后端 Default），点 Lip-sync 卡片时为 "Lip-Sync MV" */
  const [externalContentCategory, setExternalContentCategory] = useState<string>('')
  /** 第一个例子（MV+音乐）加载中，防止未加载完就点创建导致数据丢失 */
  /** Lip-sync 模版加载中（加载 demo-song-lipsync.mp3） */
  const [isLipsyncDemoLoading, setIsLipsyncDemoLoading] = useState(false)
  /** Storytelling 模版加载中 */
  const [isStorytellingDemoLoading, setIsStorytellingDemoLoading] = useState(false)
  /** Product Launch 模版加载中 */
  const [isProductLaunchDemoLoading, setIsProductLaunchDemoLoading] = useState(false)
  /** 新年换过年头像弹窗 */
  const [newYearAvatarModalOpen, setNewYearAvatarModalOpen] = useState(false)
  const newYearAvatarInputRef = useRef<HTMLInputElement>(null)
  /** 弹窗内 guonian1/guonian2 切换索引 */
  const [guonianImageIndex, setGuonianImageIndex] = useState(0)
  /** 触发 GenerationBox 自动发送（卡片1上传头像后） */
  const [autoSendTrigger, setAutoSendTrigger] = useState(0)

  useEffect(() => {
    if (!newYearAvatarModalOpen) return
    const id = setInterval(() => {
      setGuonianImageIndex(i => (i === 0 ? 1 : 0))
    }, 2000)
    return () => clearInterval(id)
  }, [newYearAvatarModalOpen])

  // Load demo audio for Lip-sync template (same pattern as Kill This Love)
  const loadLipsyncDemoAssets = useCallback(async () => {
    if (isLipsyncDemoLoading) return
    setIsLipsyncDemoLoading(true)
    try {
      setExternalContentCategory('Lip-Sync MV')
      setExternalPrompt(t('selectionHubDemoLipsyncPrompt'))
      setExternalFiles([])
      setPromptKey(k => k + 1)
      await new Promise(r => setTimeout(r, 50))

      const imageResponse = await fetch(demoCharacterLipsync)
      if (!imageResponse.ok) throw new Error(`Image failed: ${imageResponse.status}`)
      const imageBlob = await imageResponse.blob()
      const imageFile = new File([imageBlob], 'demo-character-lipsync.png', { type: 'image/png' })

      const audioResponse = await fetch(lipsyncDemoAudioUrl)
      if (!audioResponse.ok) throw new Error(`Audio failed: ${audioResponse.status}`)
      const audioBlob = await audioResponse.blob()
      if (audioBlob.size === 0) throw new Error('Audio file is empty')
      const buffer = await audioBlob.arrayBuffer()
      const materializedBlob = new Blob([buffer], { type: 'audio/mpeg' })
      const audioFile = new File([materializedBlob], 'make-your-music-video-example.mp3', { type: 'audio/mpeg' })

      setExternalFiles([imageFile, audioFile])
      setPromptKey(k => k + 1)
      toast.success('Lip-sync demo loaded!')

      setTimeout(() => {
        const generationBox = document.querySelector('[data-generation-box]')
        if (generationBox) generationBox.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 100)
    } catch (error) {
      console.error('Failed to load lipsync demo audio:', error)
      setExternalPrompt(t('selectionHubDemoLipsyncPrompt'))
      toast.error('Failed to load demo audio. Please try again.')
    } finally {
      setIsLipsyncDemoLoading(false)
    }
  }, [isLipsyncDemoLoading, t])

  const loadProductLaunchDemoAssets = useCallback(async () => {
    if (isProductLaunchDemoLoading) return
    setIsProductLaunchDemoLoading(true)
    try {
      setExternalContentCategory('Product Launch')
      setExternalPrompt(t('selectionHubDemoProductLaunchPrompt'))
      setExternalFiles([])
      setPromptKey(k => k + 1)
      await new Promise(r => setTimeout(r, 50))

      const imageResponse = await fetch(demoProductLaunchImage)
      if (!imageResponse.ok) throw new Error(`Image failed: ${imageResponse.status}`)
      const imageBlob = await imageResponse.blob()
      const imageFile = new File([imageBlob], 'demo-product-launch.webp', { type: 'image/webp' })

      setExternalFiles([imageFile])
      setPromptKey(k => k + 1)
      toast.success('Product Launch demo loaded!')

      setTimeout(() => {
        const generationBox = document.querySelector('[data-generation-box]')
        if (generationBox) generationBox.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 100)
    } catch (error) {
      console.error('Failed to load product launch demo:', error)
      setExternalPrompt(t('selectionHubDemoProductLaunchPrompt'))
      toast.error('Failed to load demo. Please try again.')
    } finally {
      setIsProductLaunchDemoLoading(false)
    }
  }, [isProductLaunchDemoLoading, t])

  const loadStorytellingDemoAssets = useCallback(async () => {
    if (isStorytellingDemoLoading) return
    setIsStorytellingDemoLoading(true)
    try {
      setExternalContentCategory('')
      setExternalPrompt('')
      setExternalFiles([])
      setPromptKey(k => k + 1)
      await new Promise(r => setTimeout(r, 50))

      const imageResponse = await fetch(demoStorytellingImage)
      if (!imageResponse.ok) throw new Error(`Image failed: ${imageResponse.status}`)
      const imageBlob = await imageResponse.blob()
      const imageFile = new File([imageBlob], 'demo-storytelling.png', { type: 'image/png' })

      setExternalFiles([imageFile])
      setExternalPrompt(t('storytellingPrompt'))
      setPromptKey(k => k + 1)
      toast.success('Storytelling demo loaded!')

      setTimeout(() => {
        const generationBox = document.querySelector('[data-generation-box]')
        if (generationBox) generationBox.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 100)
    } catch (error) {
      console.error('Failed to load storytelling demo:', error)
      setExternalPrompt(t('storytellingPrompt'))
      toast.error('Failed to load demo. Please try again.')
    } finally {
      setIsStorytellingDemoLoading(false)
    }
  }, [isStorytellingDemoLoading, t])

  const handleCreateClick = () => {
    // Scroll to generation box and focus the textarea
    const generationBox = document.querySelector('[data-generation-box]')
    if (generationBox) {
      generationBox.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setTimeout(() => {
        const textarea = generationBox.querySelector('textarea')
        if (textarea) textarea.focus()
      }, 200)
    }
  }

  const handleNewYearAvatarImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !file.type.startsWith('image/')) return
    setExternalContentCategory('')
    setExternalFiles([file])
    setExternalPrompt(NEW_YEAR_AVATAR_DISPLAY_PROMPT)
    setPromptKey(k => k + 1)
    setNewYearAvatarModalOpen(false)
    e.target.value = ''
    setTimeout(() => {
      const generationBox = document.querySelector('[data-generation-box]')
      if (generationBox) generationBox.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 100)
    setTimeout(() => setAutoSendTrigger(t => t + 1), 800)
  }

  return (
    <div className="min-h-screen bg-background flex overflow-x-hidden">
      <Sidebar onCreateClick={handleCreateClick} hideUserFooter />
      <MobileBottomNav onCreateClick={handleCreateClick} />

      <main className="flex-1 min-w-0 md:ml-20 flex flex-col items-center justify-center px-4 md:px-8 pb-20 md:pb-0 overflow-x-hidden">
        <div className="max-w-3xl w-full min-w-0 pt-8 md:pt-16 pb-12">
          <HeroSection />

          {/* Cuti welcome message above dialog - message bubble style */}
          <div className="mt-6 md:mt-8 flex items-start gap-3 sm:gap-4">
            <Avatar className="h-10 w-10 sm:h-12 sm:w-12 rounded-full flex-shrink-0 border-2 border-primary/20">
              <AvatarImage src={exploreChatRobot} alt="Cuti" className="object-cover" />
              <AvatarFallback className="bg-primary/10 text-primary">Cuti</AvatarFallback>
            </Avatar>
            <div className="rounded-2xl bg-card px-4 py-3 shadow-md border border-border max-w-[85%] sm:max-w-[90%]">
              <p className="font-inter whitespace-pre-line text-sm sm:text-base text-foreground leading-relaxed">
                {(() => {
                  const welcomeText = t('homeCutiWelcome')
                  const highlightText = language === 'zh' ? '音乐视频' : 'music videos'
                  const parts = welcomeText.split(highlightText)
                  if (parts.length === 2) {
                    return (
                      <>
                        {parts[0]}
                        <span className="bg-gradient-to-r from-pink-500 via-purple-500 to-violet-600 bg-clip-text text-transparent font-semibold">{highlightText}</span>
                        {parts[1]}
                      </>
                    )
                  }
                  return welcomeText
                })()}
              </p>
            </div>
          </div>

          <div className="mt-8 sm:mt-10 md:mt-12" data-generation-box>
            <GenerationBox
              key={promptKey}
              externalPrompt={externalPrompt}
              externalFiles={externalFiles}
              externalContentCategory={externalContentCategory || undefined}
              autoSendTrigger={autoSendTrigger}
            />
          </div>

          {/* Shortcut Cards - 移动端纵向堆叠，桌面端横向排列。顺序：1 音乐视频 2 故事视频 3 新年头像 */}
          <div className="mt-8 sm:mt-10 md:mt-12 flex flex-col md:flex-row gap-3 md:gap-4 w-full">
            {/* Cuti做个音乐视频：第一位 */}
            <div className="flex-1 flex flex-col gap-2">
              <p className="text-sm sm:text-base font-semibold text-center text-foreground order-first md:order-2">{t('selectionHubCardMusicVideo')}</p>
              <button
                type="button"
                onClick={loadLipsyncDemoAssets}
                disabled={isLipsyncDemoLoading}
                className="relative overflow-hidden rounded-2xl border border-border/50 hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5 transition-all duration-300 group cursor-pointer aspect-video disabled:opacity-60 disabled:cursor-not-allowed w-full order-2 md:order-1"
              >
                <img
                  src={lipsyncCardBg}
                  alt={t('selectionHubLipsyncAlt')}
                  className="absolute inset-0 w-full h-full object-cover"
                />
                <div className="absolute inset-0 bg-gradient-to-r from-black/20 to-transparent" />
                {isLipsyncDemoLoading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40 text-white text-sm font-medium">
                    {t('selectionHubDemoLoading')}
                  </div>
                )}
              </button>
            </div>

            {/* Cuti做个故事视频：第二位 */}
            <div className="flex-1 flex flex-col gap-2">
              <p className="text-sm sm:text-base font-semibold text-center text-foreground order-first md:order-2">{t('selectionHubCardStoryVideo')}</p>
              <button
                type="button"
                onClick={loadStorytellingDemoAssets}
                disabled={isStorytellingDemoLoading}
                className="relative overflow-hidden rounded-2xl border border-border/50 hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5 transition-all duration-300 group cursor-pointer aspect-video disabled:opacity-60 disabled:cursor-not-allowed w-full order-2 md:order-1"
              >
                <img
                  src={storytellingCardBg}
                  alt={t('selectionHubStoryAlt')}
                  className="absolute inset-0 w-full h-full object-cover"
                />
                <div className="absolute inset-0 bg-gradient-to-r from-black/20 to-transparent" />
                {isStorytellingDemoLoading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40 text-white text-sm font-medium">
                    {t('selectionHubDemoLoading')}
                  </div>
                )}
              </button>
            </div>

            {/* Cuti做个产品发布视频 */}
            <div className="flex-1 flex flex-col gap-2">
              <p className="text-sm sm:text-base font-semibold text-center text-foreground order-first md:order-2">{t('selectionHubCardProductLaunch')}</p>
              <button
                type="button"
                onClick={loadProductLaunchDemoAssets}
                disabled={isProductLaunchDemoLoading}
                className="relative overflow-hidden rounded-2xl border border-border/50 hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5 transition-all duration-300 group cursor-pointer aspect-video disabled:opacity-60 disabled:cursor-not-allowed w-full order-2 md:order-1"
              >
                <img
                  src={demoProductLaunchImage}
                  alt={t('selectionHubProductAlt')}
                  className="absolute inset-0 w-full h-full object-cover"
                />
                <div className="absolute inset-0 bg-gradient-to-r from-black/20 to-transparent" />
                {isProductLaunchDemoLoading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40 text-white text-sm font-medium">
                    {t('selectionHubDemoLoading')}
                  </div>
                )}
              </button>
            </div>

            {/* Cuti 给你做新年头像：第三位 */}
            <div className="hidden flex-1 flex flex-col gap-2">
              <p className="text-sm sm:text-base font-semibold text-center text-foreground order-first md:order-2">{t('selectionHubCardNewYearAvatar')}</p>
              <button
                type="button"
                onClick={() => setNewYearAvatarModalOpen(true)}
                className="relative overflow-hidden rounded-2xl border-2 border-transparent hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5 transition-all duration-300 group cursor-pointer aspect-video disabled:opacity-60 disabled:cursor-not-allowed w-full order-2 md:order-1"
              >
                <img
                  src={`${import.meta.env.BASE_URL}chunjie-cover.jpg`}
                  alt={t('selectionHubNewYearAlt')}
                  className="absolute inset-0 w-full h-full object-cover"
                />
              </button>
            </div>
          </div>

          {/* 新年换过年头像 - 上传头像弹窗 */}
          <input
            ref={newYearAvatarInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleNewYearAvatarImageSelect}
          />
          <Dialog open={newYearAvatarModalOpen} onOpenChange={setNewYearAvatarModalOpen}>
            <DialogContent className="max-w-[90vw] sm:max-w-sm rounded-2xl text-center">
              <div className="flex flex-col items-center gap-4 pt-2">
                <div className="flex h-14 w-14 items-center justify-center rounded-full bg-red-500 text-2xl font-bold text-amber-400 shadow-md">
                  福
                </div>
                <div>
                  <h2 className="text-xl font-bold text-foreground">{t('selectionHubNewYearUploadTitle')}</h2>
                  <p className="mt-1.5 text-sm text-muted-foreground">
                    {t('selectionHubNewYearUploadHint')}
                  </p>
                  <img
                    src={guonianImageIndex === 0 ? `${import.meta.env.BASE_URL}guonian1.jpg` : `${import.meta.env.BASE_URL}guonian2.jpg`}
                    alt=""
                    className="mt-3 mx-auto max-h-32 w-auto object-contain"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => newYearAvatarInputRef.current?.click()}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-neutral-800 px-4 py-3 text-white hover:bg-neutral-700"
                >
                  <ImageIcon className="h-5 w-5" />
                  {t('selectionHubChooseImage')}
                </button>
                <button
                  type="button"
                  onClick={() => setNewYearAvatarModalOpen(false)}
                  className="text-sm text-muted-foreground hover:text-foreground"
                >
                  {t('cancel')}
                </button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </main>
    </div>
  )
}

export default SelectionHub
