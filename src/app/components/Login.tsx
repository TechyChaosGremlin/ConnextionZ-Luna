import { useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import {
  Eye, EyeOff, Mail, 
  Lock, ArrowRight, ArrowLeft,
  AlertCircle
} from "lucide-react";
import {
  signIn, PROVIDER_LABEL, type Account,
} from "../auth-store";
import { 
  Checkbox, GUTTER, H1, 
  isValidEmail, Input, SCREEN, 
  SUB, TOP_PAD 
} from '../Auth'

import AppleMark                           from "./ui/AppleMark"
import Divider                             from "./ui/Divider"
import GoogleMark                          from "./ui/GoogleMark"
import Logo                                from "./ui/Logo"
import PrimaryBtn                          from "./ui/PrimaryBtn"
import SocialBtn                           from "./ui/SocialBtn"

export default function Login({
  onLogin, onCreate, onForgot, onBack,
}: { onLogin: (account: Account) => void; onCreate: () => void; onForgot: () => void; onBack: () => void }) {
  const [email, setEmail]                 = useState("");
  const [password, setPassword]           = useState("");
  const [showPw, setShowPw]               = useState(false);
  const [remember, setRemember]           = useState(false);
  const [loading, setLoading]             = useState(false);
  const [errors, setErrors]               = useState<{ email?: string; password?: string; general?: string }>({});

  const valid = isValidEmail(email) && password.length >= 6;

  const handleLogin = async () => {
    const errs: typeof errors = {};
    if (!isValidEmail(email)) errs.email    = "Enter a valid email address";
    if (password.length < 6)  errs.password = "Password must be at least 6 characters";

    if (Object.keys(errs).length) { 
      setErrors(errs); 
      return; 
    }

    setLoading(true);
    setErrors({});
    const result = await signIn(email, password);
    setLoading(false);

    // Only authenticate when the credentials actually match.
    if (!result.ok) { 
      setErrors({ general: result.error }); 
      return; 
    }

    onLogin(result.value);
  };

  /** Clear the "incorrect credentials" banner as soon as the user edits either field. */
  const clearGeneral = () => setErrors((e) => (e.general ? { ...e, general: undefined } : e));

  return (
    <div className={`${SCREEN} overflow-y-auto lg:overflow-visible`}>
      {/* Header — the logo is redundant next to the desktop brand panel. */}
      <div className={`flex items-center justify-between px-6 ${TOP_PAD} pb-6 lg:pb-8 lg:px-0`}>
        <button onClick={onBack} className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: "rgba(0,60,130,0.35)", border: "1px solid rgba(0,174,239,0.15)" }}>
          <ArrowLeft className="w-4 h-4 text-white" />
        </button>
        <span className="lg:hidden"><Logo size="sm" /></span>
        <div className="w-9" />
      </div>

      <div className={`${GUTTER} flex-1 space-y-6 pb-10 lg:pb-0`}>
        <div>
          <h1 className={H1}>Welcome back 👋</h1>
          <p className={`${SUB} mt-1`}>Log in to continue creating</p>
        </div>

        {/* Error banner */}
        <AnimatePresence>
          {errors.general && (
            <motion.div 
            initial={{ opacity: 0, y: -8 }} 
            animate={{ opacity: 1, y: 0  }} 
            exit=   {{ opacity: 0, y: -8 }}
              className="flex items-center gap-3 px-4 py-3 rounded-2xl"
              style={{ background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)" }}>
              <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
              <span className="text-red-400 text-[13px]">{errors.general}</span>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="space-y-4">
          <Input label="Email" type="email" value={email} onChange={(v) => { setEmail(v); clearGeneral(); }} placeholder="you@example.com"
            icon={<Mail className="w-4 h-4" />} error={errors.email} autoFocus />
          <Input label="Password" type={showPw ? "text" : "password"} value={password} onChange={(v) => { setPassword(v); clearGeneral(); }}
            placeholder="••••••••" icon={<Lock className="w-4 h-4" />} error={errors.password}
            rightEl={
              <button onClick={() => setShowPw((p) => !p)} className="text-white/40 hover:text-white/70 transition-colors">
                {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            } />
        </div>

        {/* Remember + Forgot */}
        <div className="flex items-center justify-between">
          <Checkbox checked={remember} onChange={setRemember}>Remember me</Checkbox>
          <button onClick={onForgot} className="text-[13px] font-semibold" style={{ color: "#00AEEF" }}>Forgot password?</button>
        </div>

        <PrimaryBtn onClick={handleLogin} disabled={!valid} loading={loading}>
          Log In <ArrowRight className="w-5 h-5" />
        </PrimaryBtn>

        <Divider />

        <div className="flex gap-3">
          <SocialBtn icon={<GoogleMark />} label="Google"
            busy={false} disabled={loading}
            onClick={() => setErrors({ general: `${PROVIDER_LABEL.google} sign-in is not configured yet.` })} />
          <SocialBtn icon={<AppleMark />} label="Apple"
            busy={false} disabled={loading}
            onClick={() => setErrors({ general: `${PROVIDER_LABEL.apple} sign-in is not configured yet.` })} />
        </div>

        <p className="text-center text-white/40 text-[12px]">Google and Apple sign-in are not configured.</p>

        <div className="text-center pb-4">
          <span className="text-white/40 text-[14px]">Don't have an account? </span>
          <button onClick={onCreate} className="font-bold text-[14px]" style={{ color: "#00AEEF" }}>Create Account</button>
        </div>
      </div>

    </div>
  );
}