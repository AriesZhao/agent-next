import { useState, useEffect, useRef, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Input, Button } from "antd";
import { message } from "@/utils/antdMessage";

import { KeyRound, Lock, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { clearAuthToken, setAuthToken } from "../../api";
import { authApi, type OauthProviderStatus } from "../../api/modules/auth";
import { apiErrorMessage } from "../../utils/apiError";
import { refreshServerLabels } from "../../i18n";
import { applyUserLocale, applyGuestLocale } from "../../utils/locale";
import { useTheme } from "../../context/ThemeContext";
import {
  isSsoPopup,
  isSsoPopupMessage,
  notifySsoOpener,
  openSsoPopup,
} from "../../utils/ssoPopup";
import feishuIcon from "../../assets/channels/feishu.svg";
import dingtalkIcon from "../../assets/channels/dingtalk.svg";
import wecomIcon from "../../assets/channels/wecom.svg";
import googleIcon from "../../assets/providers/google.svg";
import CaptchaField, { type CaptchaFieldHandle } from "./CaptchaField";
import { type PublicCaptchaConfig } from "./captchaAdapters";

function providerLabel(
  provider: OauthProviderStatus,
  t: (key: string, opts?: Record<string, string>) => string,
): string {
  const name = provider.display_name.trim();
  if (name) return name;
  return t(`login.providerKind.${provider.kind}`, {
    defaultValue: provider.kind,
  });
}

function providerIcon(provider: OauthProviderStatus): ReactNode {
  if (provider.kind === "feishu") {
    return (
      <img src={feishuIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  if (provider.kind === "dingtalk") {
    return (
      <img src={dingtalkIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  if (provider.kind === "wecom") {
    return (
      <img src={wecomIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  const name = provider.display_name.trim().toLowerCase();
  if (
    provider.kind === "oidc" &&
    (name === "google" || name.includes("google"))
  ) {
    return (
      <img src={googleIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  return <KeyRound size={18} />;
}

export default function LoginPage() {
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [providers, setProviders] = useState<OauthProviderStatus[]>([]);
  const [ssoLoadingKind, setSsoLoadingKind] = useState<string | null>(null);
  const [captchaReady, setCaptchaReady] = useState(false);
  const [captchaResetKey, setCaptchaResetKey] = useState(0);
  const [captcha, setCaptcha] = useState<PublicCaptchaConfig>({
    provider: "slider",
  });
  const captchaRef = useRef<CaptchaFieldHandle>(null);

  useEffect(() => {
    void applyGuestLocale();
  }, []);

  useEffect(() => {
    let cancelled = false;
    authApi
      .getAuthStatus()
      .then((status) => {
        if (cancelled) return;
        if (status.setup_required) {
          clearAuthToken();
          navigate("/setup", { replace: true });
          return;
        }
        authApi
          .getOauthStatus()
          .then((next) => {
            if (!cancelled) {
              setProviders(next.providers.filter((item) => item.enabled));
            }
          })
          .catch(() => {});
        authApi
          .getCaptcha()
          .then((next) => {
            if (!cancelled) setCaptcha(next);
          })
          .catch(() => {
            if (!cancelled) setCaptcha({ provider: "slider" });
          });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  useEffect(() => {
    const code = searchParams.get("oidc_error");
    if (!code) return;
    if (notifySsoOpener({ ok: false, error: code })) return;
    message.error(
      t(`login.oidcError.${code}`, {
        defaultValue: t("login.oidcError.generic"),
      }),
    );
    navigate("/login", { replace: true });
  }, [navigate, searchParams, t]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!isSsoPopupMessage(event, window.location.origin) || !event.data.ok) {
        if (
          isSsoPopupMessage(event, window.location.origin) &&
          !event.data.ok
        ) {
          setSsoLoadingKind(null);
          const code = event.data.error || "generic";
          message.error(
            t(`login.oidcError.${code}`, {
              defaultValue: t("login.oidcError.generic"),
            }),
          );
        }
        return;
      }
      window.location.replace(event.data.redirect || "/chat");
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [t]);

  const resetCaptcha = () => {
    setCaptchaReady(false);
    setCaptchaResetKey((k) => k + 1);
  };

  const onSso = async (kind: string) => {
    setSsoLoadingKind(kind);
    let popup: Window | null = null;
    if (kind !== "oidc") {
      popup = openSsoPopup();
    }
    try {
      const { authorization_url } = await authApi.startOauth(kind, "/chat");
      if (kind === "oidc") {
        window.location.href = authorization_url;
        return;
      }
      if (popup && !popup.closed) {
        popup.location.href = authorization_url;
        const timer = window.setInterval(() => {
          if (!popup || popup.closed) {
            window.clearInterval(timer);
            setSsoLoadingKind((current) => (current === kind ? null : current));
          }
        }, 400);
      } else {
        popup?.close();
        message.error(t("account.ssoPopupBlocked"));
        setSsoLoadingKind(null);
      }
    } catch (err) {
      popup?.close();
      message.error(apiErrorMessage(err, t("login.oidcStartFailed"), t));
      setSsoLoadingKind(null);
    }
  };

  const handleLogin = async () => {
    if (!username || !password || !captchaReady) return;
    setLoading(true);
    try {
      const token = await captchaRef.current?.getToken();
      const res = await authApi.login(username, password, token);
      setAuthToken(res.access_token);
      await applyUserLocale(res.user.locale);
      void refreshServerLabels(res.user.locale);
      navigate("/chat", { replace: true });
    } catch (err) {
      message.error(apiErrorMessage(err, t("login.failed"), t));
      resetCaptcha();
    } finally {
      setLoading(false);
    }
  };

  if (isSsoPopup() && searchParams.get("oidc_error")) {
    return null;
  }

  const logoSrc = isDark ? "/logo_name_dark.png" : "/logo_name.png";
  const markSrc = "/logo_name.png";

  return (
    <div className="auth-page">
      {/* Left decorative panel */}
      <aside className="auth-left auth-mesh">
        <div>
          <div className="auth-left-brand">
            <img src={markSrc} alt="AriesAgent" />
            <div>
              <div className="auth-left-brand-name">AriesAgent</div>
              <div className="auth-left-brand-sub">AI Agent Platform</div>
            </div>
          </div>

          <h2 className="auth-headline" style={{ marginTop: 48 }}>
            {t("login.headline")}
            <br />
            <span className="auth-gradient-text">
              {t("login.headlineHighlight")}
            </span>
          </h2>

          <p className="auth-description">{t("login.description")}</p>

          <ul className="auth-features">
            {[
              t("login.feature1"),
              t("login.feature2"),
              t("login.feature3"),
            ].map((text) => (
              <li key={text} className="auth-feature-item">
                <span className="auth-feature-check">✓</span>
                {text}
              </li>
            ))}
          </ul>
        </div>

        <p className="auth-left-footer">© AriesAgent</p>
      </aside>

      {/* Right form panel */}
      <div className="auth-right">
        <header className="auth-header">
          <div className="auth-header-brand">
            <img src={markSrc} alt="AriesAgent" />
            <span>AriesAgent</span>
          </div>
        </header>

        <main className="auth-main">
          <div className="auth-card">
            <img src={logoSrc} alt="AriesAgent" className="auth-card-logo" />

            <h2 className="auth-card-title">{t("login.title")}</h2>

            <div className="auth-form">
              <Input
                prefix={
                  <User
                    size={16}
                    style={{ color: "var(--fn-text-quaternary)" }}
                  />
                }
                placeholder={t("login.username")}
                size="large"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                style={{ borderRadius: 10 }}
              />

              <Input.Password
                prefix={
                  <Lock
                    size={16}
                    style={{ color: "var(--fn-text-quaternary)" }}
                  />
                }
                placeholder={t("login.password")}
                size="large"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onPressEnter={handleLogin}
                style={{ borderRadius: 10 }}
              />

              <CaptchaField
                ref={captchaRef}
                config={captcha}
                resetKey={captchaResetKey}
                slideHint={t("login.slideHint")}
                slideVerifiedLabel={t("login.slideVerified")}
                unsupportedLabel={t("login.unsupportedCaptcha")}
                onReadyChange={setCaptchaReady}
              />

              <Button
                type="primary"
                size="large"
                block
                loading={loading}
                onClick={handleLogin}
                disabled={!username || !password || !captchaReady}
                className="auth-submit-btn"
              >
                {t("login.submit")}
              </Button>

              {providers.length > 0 && (
                <>
                  <div className="auth-divider">
                    <span className="auth-divider-line" />
                    {t("login.or")}
                    <span className="auth-divider-line" />
                  </div>
                  {providers.map((provider) => (
                    <Button
                      key={provider.kind}
                      size="large"
                      block
                      icon={providerIcon(provider)}
                      loading={ssoLoadingKind === provider.kind}
                      onClick={() => void onSso(provider.kind)}
                      className="auth-sso-btn"
                    >
                      {t("login.oidcWith", {
                        name: providerLabel(provider, t),
                      })}
                    </Button>
                  ))}
                </>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
