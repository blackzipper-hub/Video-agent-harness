import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { ArrowLeft, Globe } from "lucide-react";
import { api } from "@/services/api";
import { useLanguage } from "@/i18n/LanguageContext";
import { useAuth } from "@/hooks/useAuth";
import { getAuthErrorMessage } from "@/utils/authApiErrors";

type SignupStep = "email" | "otp" | "register";

const AuthPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t, language, setLanguage } = useLanguage();
  const { checkAuth } = useAuth();
  const [loading, setLoading] = useState(false);
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [otpCode, setOtpCode] = useState(["", "", "", "", "", ""]);
  const otpInputRefs = useRef<(HTMLInputElement | null)[]>([]);

  const [signupStep, setSignupStep] = useState<SignupStep>("email");
  const [verifiedEmail, setVerifiedEmail] = useState("");
  const [otpExpiresAt, setOtpExpiresAt] = useState<string | null>(null);
  const [resendCountdown, setResendCountdown] = useState(0);
  const isVerifyingRef = useRef(false);

  const goBackToSignupEmail = () => {
    isVerifyingRef.current = false;
    setLoading(false);
    setSignupStep("email");
    setVerifiedEmail("");
    setOtpCode(["", "", "", "", "", ""]);
    setResendCountdown(0);
    setOtpExpiresAt(null);
  };

  // 获取重定向目标路径
  const returnTo = location.state?.returnTo || "/";
  const loginMessage = location.state?.message;

  // Countdown timer for resend OTP
  useEffect(() => {
    if (resendCountdown > 0) {
      const timer = setTimeout(() => {
        setResendCountdown(resendCountdown - 1);
      }, 1000);
      return () => clearTimeout(timer);
    }
  }, [resendCountdown]);

  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    if (!email || !email.includes("@")) {
      toast.error(t("authPleaseEnterValidEmail"));
      setLoading(false);
      return;
    }

    try {
      const response = await api.auth.sendOtp(email);

      if (response.code === 0 || response.code === 200) {
        toast.success(t("otpSentSuccess"));
        setVerifiedEmail(email);
        setOtpExpiresAt(response.data.expires_at);
        setResendCountdown(60); // 60 second countdown
        setOtpCode(["", "", "", "", "", ""]);
        isVerifyingRef.current = false;
        setSignupStep("otp");
      } else {
        toast.error(getAuthErrorMessage({ message: response.message }, t, "authFailedSendVerificationCode"));
      }
    } catch (error: unknown) {
      toast.error(getAuthErrorMessage(error, t, "authFailedSendVerificationCode"));
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = useCallback(async () => {
    if (isVerifyingRef.current) return; // Prevent multiple simultaneous calls

    isVerifyingRef.current = true;
    setLoading(true);

    const codeString = otpCode.join("");

    if (codeString.length !== 6) {
      toast.error(t("authPleaseEnterAll6Digits"));
      setLoading(false);
      isVerifyingRef.current = false;
      return;
    }

    try {
      const response = await api.auth.verifyOtp(verifiedEmail, codeString);

      if (response.code === 0 || response.code === 200) {
        if (response.data.valid) {
          toast.success(t("otpVerifiedSuccess"));
          setSignupStep("register");
        } else {
          // Handle specific error messages
          const message = response.data.message || "";
          if (message.includes("expired") || message.includes("过期")) {
            toast.error(t("otpExpired"));
          } else if (message.includes("Too many") || message.includes("次数过多") || message.includes("过于频繁")) {
            toast.error(t("otpTooManyAttempts"));
          } else if (message.includes("No verification") || message.includes("未找到")) {
            toast.error(t("otpNotFound"));
          } else {
            toast.error(t("otpInvalid"));
          }
        }
      } else {
        toast.error(getAuthErrorMessage({ message: response.message }, t, "authFailedVerifyOtp"));
      }
    } catch (error: unknown) {
      toast.error(getAuthErrorMessage(error, t, "authFailedVerifyOtp"));
    } finally {
      setLoading(false);
      isVerifyingRef.current = false;
    }
  }, [otpCode, verifiedEmail, t]);

  const handleOtpCodeChange = (index: number, value: string) => {
    if (value.length > 1) {
      value = value.slice(-1);
    }

    // Only allow digits
    if (value && !/^\d$/.test(value)) {
      return;
    }

    const newCode = [...otpCode];
    newCode[index] = value;
    setOtpCode(newCode);

    if (value && index < 5) {
      otpInputRefs.current[index + 1]?.focus();
    }
  };

  // Auto-verify OTP when all 6 digits are entered (only on OTP step)
  useEffect(() => {
    if (signupStep !== "otp") return;
    const codeString = otpCode.join("");
    if (codeString.length === 6) {
      handleVerifyOtp();
    }
  }, [otpCode, handleVerifyOtp, signupStep]);

  const handleOtpKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !otpCode[index] && index > 0) {
      otpInputRefs.current[index - 1]?.focus();
    }
  };

  const handleOtpPaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault();
    const pastedData = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
    const newCode = [...otpCode];

    for (let i = 0; i < pastedData.length && i < 6; i++) {
      newCode[i] = pastedData[i];
    }

    setOtpCode(newCode);

    const nextEmptyIndex = newCode.findIndex((code) => !code);
    if (nextEmptyIndex !== -1) {
      otpInputRefs.current[nextEmptyIndex]?.focus();
    } else {
      otpInputRefs.current[5]?.focus();
    }
  };

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const response = await api.auth.register({
        email,
        password,
      });

      if (response.code === 0) {
        toast.success(t("authRegistrationSuccessRedirecting"));
        toast.success(t("registerSuccessInviteCodes"), { duration: 6000 });
        // Update auth state after successful registration
        await checkAuth();
        navigate(returnTo);
      } else {
        toast.error(getAuthErrorMessage({ message: response.message }, t, "authRegistrationFailed"));
      }
    } catch (error: unknown) {
      toast.error(getAuthErrorMessage(error, t, "authSignUpFailedRetry"));
    } finally {
      setLoading(false);
    }
  };

  const handleTabChange = (value: string) => {
    if (value === "signup") {
      setSignupStep("email");
      setVerifiedEmail("");
      setOtpCode(["", "", "", "", "", ""]);
      setEmail("");
      setPassword("");
      setOtpExpiresAt(null);
    } else if (value === "signin") {
      setEmail("");
      setPassword("");
    }
  };

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const response = await api.auth.loginWithPassword({
        email,
        password,
      });

      console.log('🔍 Login response:', response);
      console.log('🍪 Cookies after login:', document.cookie);

      if (response.code === 0) {
        toast.success(t("authSignInSuccess"));
        // Update auth state after successful login
        console.log('🔄 Calling checkAuth after login...');

        // Wait a bit to ensure cookie is set
        await new Promise(resolve => setTimeout(resolve, 200));
        console.log('🍪 Cookies before checkAuth:', document.cookie);

        await checkAuth();
        console.log('✅ checkAuth completed, navigating to:', returnTo);
        // Small delay to ensure state updates propagate
        setTimeout(() => {
          navigate(returnTo);
        }, 100);
      } else {
        toast.error(getAuthErrorMessage({ message: response.message }, t, "authLoginFailed"));
      }
    } catch (error: unknown) {
      console.error('❌ Login error:', error);
      toast.error(getAuthErrorMessage(error, t, "authSignInFailedCredentials"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
      <div className="w-full max-w-xl">
        <div className="flex items-center justify-between mb-8">
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={() => navigate("/")}
            className="gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            {t("backToHome")}
          </Button>
          <img src={`${import.meta.env.BASE_URL}logo.svg`} alt="Cuti" className="h-12" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setLanguage(language === "en" ? "zh" : "en")}
            className="gap-1.5"
          >
            <Globe className="w-4 h-4" />
            {language === "en" ? "EN" : "中文"}
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>{t("authWelcomeTitle")}</CardTitle>
            <CardDescription>
              {loginMessage || t("authWelcomeDescription")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="signin" className="w-full" onValueChange={handleTabChange}>
              <TabsList className="grid w-full grid-cols-2 h-12 p-1 rounded-lg items-stretch md:h-10">
                <TabsTrigger
                  value="signin"
                  className="h-full min-h-0 py-0 text-center rounded-l-md rounded-r-none data-[state=active]:shadow-none"
                >
                  {t("signIn")}
                </TabsTrigger>
                <TabsTrigger
                  value="signup"
                  className="h-full min-h-0 py-0 text-center rounded-r-md rounded-l-none data-[state=active]:shadow-none"
                >
                  {t("signUp")}
                </TabsTrigger>
              </TabsList>

              <TabsContent value="signin">
                <form onSubmit={handleSignIn} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="signin-email">{t("email")}</Label>
                    <Input
                      id="signin-email"
                      type="email"
                      placeholder="your@email.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="signin-password">{t("password")}</Label>
                    <Input
                      id="signin-password"
                      type="password"
                      placeholder="••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                    />
                  </div>
                  <Button type="submit" className="w-full" disabled={loading}>
                    {loading ? t("authSigningIn") : t("signIn")}
                  </Button>
{/* 
                  <div className="relative my-4">
                    <div className="absolute inset-0 flex items-center">
                      <span className="w-full border-t" />
                    </div>
                    <div className="relative flex justify-center text-xs uppercase">
                      <span className="bg-background px-2 text-muted-foreground">
                        Or continue with
                      </span>
                    </div>
                  </div>

                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    onClick={handleGoogleSignIn}
                  >
                    <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24">
                      <path
                        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                        fill="#4285F4"
                      />
                      <path
                        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                        fill="#34A853"
                      />
                      <path
                        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                        fill="#FBBC05"
                      />
                      <path
                        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                        fill="#EA4335"
                      />
                    </svg>
                    Continue with Google
                  </Button> */}
                </form>
              </TabsContent>

              <TabsContent value="signup">
                {signupStep === "email" && (
                  <form onSubmit={handleSendOtp} className="space-y-4">
                    <div className="text-center mb-4">
                      <h3 className="text-lg font-semibold mb-2">{t("enterEmail")}</h3>
                      <p className="text-sm text-muted-foreground">
                        {t("enterEmailForVerification")}
                      </p>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="signup-email">{t("emailAddress")}</Label>
                      <Input
                        id="signup-email"
                        type="email"
                        placeholder="your@email.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        required
                      />
                    </div>

                    <Button type="submit" className="w-full" disabled={loading}>
                      {loading ? t("sendingCode") : t("sendVerificationCode")}
                    </Button>
                  </form>
                )}

                {signupStep === "otp" && (
                  <div className="space-y-6">
                    <div className="text-center space-y-4 mt-6">
                      <h3 className="text-2xl font-semibold">{t("checkYourEmailToContinue")}</h3>
                      <div className="text-sm text-muted-foreground space-y-1">
                        <p>{t("weveSentOtpToEmail")}</p>
                        <p>{t("pleaseCheckInboxAt")}</p>
                        <p className="font-medium text-foreground">{verifiedEmail}</p>
                      </div>
                    </div>

                    <div className="flex justify-center gap-2 sm:gap-3 py-4">
                      {otpCode.map((digit, index) => (
                        <input
                          key={index}
                          ref={(el) => (otpInputRefs.current[index] = el)}
                          type="text"
                          inputMode="numeric"
                          maxLength={1}
                          value={digit}
                          onChange={(e) => handleOtpCodeChange(index, e.target.value)}
                          onKeyDown={(e) => handleOtpKeyDown(index, e)}
                          onPaste={handleOtpPaste}
                          className="w-11 h-13 sm:w-12 sm:h-14 text-center text-xl sm:text-2xl font-bold bg-background border-2 border-input rounded-lg focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
                        />
                      ))}
                    </div>

                    <div className="text-center">
                      {resendCountdown > 0 ? (
                        <p className="text-sm text-muted-foreground">
                          {t("resendIn")} {resendCountdown}s
                        </p>
                      ) : (
                        <Button
                          type="button"
                          variant="link"
                          size="sm"
                          className="text-sm"
                          onClick={async () => {
                            setLoading(true);
                            try {
                              await api.auth.sendOtp(verifiedEmail);
                              toast.success(t("otpSentSuccess"));
                              setOtpCode(["", "", "", "", "", ""]);
                              setResendCountdown(60);
                            } catch (error) {
                              toast.error(getAuthErrorMessage(error, t, "authFailedSendVerificationCode"));
                            } finally {
                              setLoading(false);
                            }
                          }}
                          disabled={loading}
                        >
                          {t("resendCodeButton")}
                        </Button>
                      )}
                    </div>

                    <div className="text-center">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={goBackToSignupEmail}
                      >
                        {t("changeEmail")}
                      </Button>
                    </div>
                  </div>
                )}

                {/* Step 4: Complete Registration */}
                {signupStep === "register" && (
                  <form onSubmit={handleSignUp} className="space-y-4">
                    <div className="space-y-2">
                      <Label className="text-sm font-medium flex items-center gap-2">
                        <span className="text-green-600">✓</span>
                        {t("emailVerified")}: {verifiedEmail}
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-auto p-0 ml-1 text-xs"
                          onClick={goBackToSignupEmail}
                        >
                          ({t("change")})
                        </Button>
                      </Label>
                    </div>

                    <div className="text-center mb-4">
                      <h3 className="text-lg font-semibold">{t("completeRegistration")}</h3>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="signup-password">{t("setYourPassword")}</Label>
                      <Input
                        id="signup-password"
                        type="password"
                        placeholder="••••••••"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                        minLength={6}
                      />
                      <p className="text-xs text-muted-foreground">
                        {t("minimumSixChars")}
                      </p>
                    </div>
                    <Button type="submit" className="w-full" disabled={loading}>
                      {loading ? t("signingUp") : t("signUp")}
                    </Button>
                  </form>
                )}
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default AuthPage;
