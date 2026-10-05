import { useState } from "react"
import {
  requestPasswordReset
} from "../auth-store";
import { 
  GUTTER, H1, Input, isValidEmail, 
  SCREEN, SUB, TOP_PAD 
} from "../Auth"
import {
  Mail, ArrowRight, ArrowLeft
} from "lucide-react";

import Logo       from "./ui/Logo"
import PrimaryBtn from "./ui/PrimaryBtn"

export default function ForgotPassword({
  onSent, onBack,
}: { onSent: (email: string, token: string | null) => void; onBack: () => void }) {
  const [email, setEmail]     = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");

  const valid = isValidEmail(email);

  const handleSend = async () => {
    if (!valid) { setError("Enter a valid email address"); return; }
    setLoading(true);
    setError("");
    const result = await requestPasswordReset(email);
    setLoading(false);
    if (!result.ok) { setError(result.error); return; }
    onSent(email, result.value.token);
  };

  return (
    <div className={SCREEN}>
      <div className={`flex items-center justify-between px-6 ${TOP_PAD} pb-6 lg:pb-8 lg:px-0`}>
        <button onClick={onBack} className="w-9 h-9 rounded-full flex items-center justify-center" style={{ background: "rgba(0,60,130,0.35)", border: "1px solid rgba(0,174,239,0.15)" }}>
          <ArrowLeft className="w-4 h-4 text-white" />
        </button>
        <span className="lg:hidden"><Logo size="sm" /></span>
        <div className="w-9" />
      </div>

      <div className={`${GUTTER} flex-1 space-y-6`}>
        {/* Icon */}
        <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: "rgba(0,174,239,0.15)", border: "1px solid rgba(0,174,239,0.3)" }}>
          <Mail className="w-7 h-7" style={{ color: "#00AEEF" }} />
        </div>

        <div>
          <h1 className={H1}>Reset password</h1>
          <p className={`${SUB} mt-2 leading-relaxed`}>
            Enter your email and we'll send you a link to get back into your account.
          </p>
        </div>

        <Input label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com"
          icon={<Mail className="w-4 h-4" />} error={error} autoFocus />

        <PrimaryBtn onClick={handleSend} disabled={!valid} loading={loading}>
          Send Reset Link <ArrowRight className="w-5 h-5" />
        </PrimaryBtn>
      </div>
    </div>
  );
}