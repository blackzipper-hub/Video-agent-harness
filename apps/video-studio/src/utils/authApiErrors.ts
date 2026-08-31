import type { TranslationKey } from "@/i18n/translations";
import { ApiBusinessError } from "@/services/api";

type TFunc = (key: TranslationKey) => string;

const AUTH_ERROR_CODE_KEYS: Partial<Record<number, TranslationKey>> = {
  10053: "authEmailAlreadyRegistered",
  10008: "authEmailOrPasswordIncorrect",
  10007: "authEmailOrPasswordIncorrect",
  10055: "authInvalidVerificationCode",
  10056: "otpExpired",
  10111: "authInvalidInviteCode",
  10112: "authInviteCodeUsed",
  10113: "authInviteCodeExpired",
  10005: "authTooManyOtpRequests",
  10201: "authEmailSendFailed",
};

function matchesMessage(message: string, patterns: string[]): boolean {
  const lower = message.toLowerCase();
  return patterns.some((p) => lower.includes(p.toLowerCase()));
}

export function resolveAuthApiError(
  t: TFunc,
  options: { code?: number; message?: string; fallback: TranslationKey }
): string {
  const { code, message, fallback } = options;

  if (code != null && AUTH_ERROR_CODE_KEYS[code]) {
    return t(AUTH_ERROR_CODE_KEYS[code]!);
  }

  const msg = message ?? "";
  if (matchesMessage(msg, ["already registered", "already exists", "邮箱已存在", "邮箱已注册"])) {
    return t("authEmailAlreadyRegistered");
  }
  if (matchesMessage(msg, ["email or password is incorrect", "incorrect email or password", "邮箱或密码"])) {
    return t("authEmailOrPasswordIncorrect");
  }
  if (matchesMessage(msg, ["email not verified", "verify your email with otp", "邮箱尚未验证", "请先验证"])) {
    return t("authEmailNotVerified");
  }
  if (matchesMessage(msg, ["too many otp", "too many verification", "请求过于频繁", "验证码请求"])) {
    return t("authTooManyOtpRequests");
  }
  if (matchesMessage(msg, ["failed to send email", "email service", "邮件发送失败", "发送验证码失败"])) {
    return t("authEmailSendFailed");
  }
  if (matchesMessage(msg, ["invite code is used", "邀请码已使用"])) {
    return t("authInviteCodeUsed");
  }
  if (matchesMessage(msg, ["invite code is expired", "邀请码已过期"])) {
    return t("authInviteCodeExpired");
  }
  if (matchesMessage(msg, ["invalid invite code", "邀请码无效"])) {
    return t("authInvalidInviteCode");
  }
  if (matchesMessage(msg, ["invalid verification code", "verification code has expired", "无效的验证码", "验证码已过期"])) {
    if (matchesMessage(msg, ["expired", "过期"])) {
      return t("otpExpired");
    }
    return t("authInvalidVerificationCode");
  }

  return t(fallback);
}

export function getAuthErrorMessage(error: unknown, t: TFunc, fallback: TranslationKey): string {
  if (error instanceof ApiBusinessError) {
    return resolveAuthApiError(t, { code: error.code, message: error.message, fallback });
  }
  if (error instanceof Error && error.message) {
    return resolveAuthApiError(t, { message: error.message, fallback });
  }
  if (typeof error === "object" && error !== null) {
    const payload = error as { code?: number; message?: string };
    if (payload.message || payload.code != null) {
      return resolveAuthApiError(t, { code: payload.code, message: payload.message, fallback });
    }
  }
  return t(fallback);
}
