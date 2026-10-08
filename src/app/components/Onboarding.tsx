import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Activity, ArrowLeft, ArrowRight, 
  Briefcase, Check, Coffee, 
  Cpu, Gamepad2, MapPin, 
  Mic, Music, Pen, 
  Star, TrendingUp, User, 
  Users, Zap 
} from "lucide-react";

import { GUTTER, Input, OnbStep, SCREEN } from "../Auth"
import Logo       from "./ui/Logo"
import PrimaryBtn from "./ui/PrimaryBtn"
import StepDots   from "./ui/StepDots"

const CREATE_CATS = [
  { icon: Music,      label: "Music" },
  { icon: Activity,   label: "Fitness" },
  { icon: MapPin,     label: "Travel" },
  { icon: Coffee,     label: "Cooking" },
  { icon: Pen,        label: "Art" },
  { icon: Cpu,        label: "Tech" },
  { icon: Gamepad2,   label: "Gaming" },
  { icon: Star,       label: "Fashion" },
  { icon: TrendingUp, label: "Business" },
];

const COLLAB_TYPES_ONB = [
  { label: "Paid Collaboration", sub: "Get compensated for your time & reach", color: "#f59e0b", bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.4)", icon: Briefcase },
  { label: "Free / Creative Collab", sub: "Create together purely for the content", color: "#22c55e", bg: "rgba(34,197,94,0.12)", border: "rgba(34,197,94,0.4)", icon: Users },
  { label: "Duet / Remix", sub: "Respond to or remix another creator's post", color: "#a78bfa", bg: "rgba(167,139,250,0.12)", border: "rgba(167,139,250,0.4)", icon: Music },
  { label: "Podcast / Interview", sub: "Feature on shows or host others on yours", color: "#f472b6", bg: "rgba(244,114,182,0.12)", border: "rgba(244,114,182,0.4)", icon: Mic },
  { label: "Brand Deal", sub: "Partner with brands for sponsored content", color: "#00AEEF", bg: "rgba(0,174,239,0.12)", border: "rgba(0,174,239,0.4)", icon: TrendingUp },
];

const AVATAR_COLORS = ["#a78bfa", "#22c55e", "#00AEEF", "#f59e0b", "#f472b6", "#ef4444"];
const RESPONSE_TIMES = ["< 1 hour", "< 4 hours", "< 24 hours"];

/** What onboarding collects. `null` when the user skipped it. */
export interface OnboardingSetup {
  categories:   string[];
  collabTypes:  string[];
  avatarColor:  string;
  creatorName:  string;
  openToCollab: boolean;
  responseTime: string;
}

export default function Onboarding({
  onDone,
  error,
}: {
  onDone: (setup: OnboardingSetup | null) => void;
  error?: string | null;
}) {
  const [step, setStep]                       = useState<OnbStep>(1);
  const [selectedCats, setSelectedCats]       = useState<string[]>([]);
  const [selectedCollabs, setSelectedCollabs] = useState<string[]>([]);
  const [avatarColor, setAvatarColor]         = useState(AVATAR_COLORS[0]);
  const [creatorName, setCreatorName]         = useState("");
  const [openToCollab, setOpenToCollab]       = useState(true);
  const [responseTime, setResponseTime]       = useState("< 4 hours");

  const toggleCat =    (l: string) => setSelectedCats((p)    => p.includes(l) ? p.filter((x) => x !== l) : [...p, l]);
  const toggleCollab = (l: string) => setSelectedCollabs((p) => p.includes(l) ? p.filter((x) => x !== l) : [...p, l]);

  const canContinue = step === 1 ? selectedCats.length > 0 : step === 2 ? selectedCollabs.length > 0 : !!creatorName.trim();

  /** Everything picked here seeds the profile and Settings, rather than being discarded. */
  const finish = () => onDone({
    categories: selectedCats,
    collabTypes: selectedCollabs,
    avatarColor,
    creatorName: creatorName.trim(),
    openToCollab,
    responseTime,
  });

  return (
    <div className={SCREEN}>
      {/* Header */}
      <div className="flex items-center justify-between px-6 pt-12 pb-4 lg:px-0 lg:pt-0">
        <span className="lg:hidden"><Logo size="sm" /></span>
        <button onClick={() => onDone(null)} className="text-white/40 text-[14px] font-semibold lg:ml-auto">Skip</button>
      </div>

      {/* Step dots */}
      <div className="flex flex-col items-center gap-1 mb-5">
        <StepDots step={step} total={3} />
        <span className="text-white/35 text-[12px]">Step {step} of 3</span>
      </div>
      {error && <p role="alert" className="px-6 pb-3 text-sm text-red-300 lg:px-0">{error}</p>}

      {/* Step content */}
      <div className={`flex-1 overflow-y-auto lg:overflow-visible ${GUTTER}`}>
        <AnimatePresence mode="wait">
          {step === 1 && (
            <motion.div key="s1" 
            initial=   {{ opacity: 0, x: 40  }} 
            animate=   {{ opacity: 1, x: 0   }} 
            exit=      {{ opacity: 0, x: -40 }} 
            transition={{ duration: 0.22     }}
            >
              <h1 className="text-white font-extrabold text-[32px] lg:text-[36px] leading-tight mb-1">What do you create?</h1>
              <p className="text-white/45 text-[14px] mb-6">Select all that apply — your feed is personalized around your picks</p>
              <div className="grid grid-cols-3 gap-3 pb-6">
                {CREATE_CATS.map(({ icon: Icon, label }) => {
                  const sel = selectedCats.includes(label);
                  return (
                    <motion.button key={label} whileTap={{ scale: 0.94 }} onClick={() => toggleCat(label)}
                      className="relative flex flex-col items-center gap-3 py-5 rounded-2xl"
                      style={{ background: sel ? "rgba(167,139,250,0.18)" : "rgba(0,40,100,0.35)", border: sel ? "1.5px solid #a78bfa" : "1.5px solid rgba(0,174,239,0.15)" }}>
                      {sel && (
                        <div className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center" style={{ background: "#a78bfa" }}>
                          <Check className="w-3 h-3 text-white" strokeWidth={3} />
                        </div>
                      )}
                      <div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: sel ? "rgba(167,139,250,0.2)" : "rgba(0,60,140,0.4)" }}>
                        <Icon className="w-5 h-5" style={{ color: sel ? "#a78bfa" : "rgba(255,255,255,0.45)" }} />
                      </div>
                      <span className="text-[13px] font-semibold" style={{ color: sel ? "#fff" : "rgba(255,255,255,0.6)" }}>{label}</span>
                    </motion.button>
                  );
                })}
              </div>
            </motion.div>
          )}

          {step === 2 && (
            <motion.div key="s2" 
            initial=   {{ opacity: 0, x: 40  }} 
            animate=   {{ opacity: 1, x: 0   }} 
            exit=      {{ opacity: 0, x: -40 }} 
            transition={{ duration: 0.22     }}
            >
              <h1 className="text-white font-extrabold text-[32px] lg:text-[36px] leading-tight mb-1">How do you collab?</h1>
              <p className="text-white/45 text-[14px] mb-6">Pick the types you're open to — creators will know how to approach you</p>
              <div className="space-y-3 pb-6">
                {COLLAB_TYPES_ONB.map(({ label, sub, color, bg, border, icon: Icon }) => {
                  const sel = selectedCollabs.includes(label);
                  return (
                    <motion.button key={label} whileTap={{ scale: 0.98 }} onClick={() => toggleCollab(label)}
                      className="w-full flex items-center gap-4 p-4 rounded-2xl text-left"
                      style={{ background: sel ? bg : "rgba(0,40,100,0.35)", border: sel ? `1.5px solid ${border}` : "1.5px solid rgba(0,174,239,0.12)" }}>
                      <div className="w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: sel ? bg : "rgba(255,255,255,0.07)" }}>
                        <Icon className="w-5 h-5" style={{ color: sel ? color : "rgba(255,255,255,0.4)" }} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-white font-bold text-[15px]">{label}</p>
                        <p className="text-white/45 text-[12px] mt-0.5">{sub}</p>
                      </div>
                      {sel && (
                        <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: color }}>
                          <Check className="w-4 h-4 text-white" strokeWidth={2.5} />
                        </div>
                      )}
                    </motion.button>
                  );
                })}
              </div>
            </motion.div>
          )}

          {step === 3 && (
            <motion.div key="s3" 
            initial=   {{ opacity: 0, x: 40  }} 
            animate=   {{ opacity: 1, x: 0   }} 
            exit=      {{ opacity: 0, x: -40 }} 
            transition={{ duration: 0.22     }}
            >
              <h1 className="text-white font-extrabold text-[30px] lg:text-[36px] leading-tight mb-1">Set up your presence</h1>
              <p className="text-white/45 text-[14px] mb-6">This is how other creators will find and recognize you</p>

              {/* Avatar */}
              <div className="flex items-center gap-5 mb-6">
                <div className="w-20 h-20 rounded-full flex items-center justify-center text-white text-3xl font-bold flex-shrink-0"
                  style={{ background: avatarColor }}>
                  {creatorName ? creatorName[0].toUpperCase() : "?"}
                </div>
                <div className="flex-1">
                  <p className="text-white/50 text-[12px] font-semibold uppercase tracking-widest mb-3">Avatar color</p>
                  <div className="flex flex-wrap gap-2">
                    {AVATAR_COLORS.map((c) => (
                      <button key={c} onClick={() => setAvatarColor(c)}
                        className="w-9 h-9 rounded-full flex items-center justify-center transition-transform hover:scale-110"
                        style={{ background: c, border: avatarColor === c ? "2.5px solid white" : "2.5px solid transparent" }}>
                        {avatarColor === c && <Check className="w-4 h-4 text-white" strokeWidth={3} />}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="space-y-4 pb-6">
                <Input label="Your creator name" value={creatorName} onChange={setCreatorName}
                  placeholder="e.g. Maya Chen" icon={<User className="w-4 h-4" />} />

                {/* Open to collabs toggle */}
                <div className="flex items-center gap-4 p-4 rounded-2xl" style={{ background: "rgba(0,40,100,0.35)", border: "1px solid rgba(0,174,239,0.18)" }}>
                  <div className="w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: "rgba(0,174,239,0.15)" }}>
                    <Users className="w-5 h-5" style={{ color: "#00AEEF" }} />
                  </div>
                  <div className="flex-1">
                    <p className="text-white font-bold text-[14px]">Open to Collaborations</p>
                    <p className="text-white/40 text-[12px]">Creators can send you collab requests</p>
                  </div>
                  <button onClick={() => setOpenToCollab((p) => !p)}
                    className="w-12 h-6 rounded-full transition-colors relative flex-shrink-0"
                    style={{ background: openToCollab ? "#00AEEF" : "rgba(255,255,255,0.15)" }}>
                    <motion.div animate={{ x: openToCollab ? 24 : 2 }} transition={{ type: "spring", damping: 20 }}
                      className="absolute top-1 w-4 h-4 rounded-full bg-white" style={{ left: 0 }} />
                  </button>
                </div>

                {/* Response time */}
                <div>
                  <p className="text-white/50 text-[12px] font-semibold uppercase tracking-widest mb-3 px-1">Typical response time</p>
                  <div className="flex gap-2">
                    {RESPONSE_TIMES.map((t) => (
                      <button key={t} onClick={() => setResponseTime(t)}
                        className="flex-1 py-2.5 rounded-full text-[13px] font-semibold transition-all"
                        style={{ background: responseTime === t ? "#00AEEF" : "rgba(0,50,120,0.35)", color: responseTime === t ? "#000" : "rgba(255,255,255,0.5)", boxShadow: responseTime === t ? "0 4px 14px rgba(0,174,239,0.35)" : "none" }}>
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Footer */}
      <div className={`${GUTTER} pb-10 pt-4 lg:pb-0 lg:pt-6 flex items-center gap-4`} style={{ borderTop: "1px solid rgba(0,174,239,0.1)" }}>
        {step > 1 && (
          <button onClick={() => setStep((s) => (s - 1) as OnbStep)}
            className="flex items-center gap-2 text-white/50 font-semibold text-[15px]">
            <ArrowLeft className="w-4 h-4" /> Back
          </button>
        )}
        <div className="flex-1">
          {step < 3
            ? <PrimaryBtn onClick={() => canContinue && setStep((s) => (s + 1) as OnbStep)} disabled={!canContinue}>
                Continue <ArrowRight className="w-5 h-5" />
              </PrimaryBtn>
            : <PrimaryBtn onClick={finish} disabled={!canContinue}>
                Start Exploring <Zap className="w-5 h-5" />
              </PrimaryBtn>
          }
        </div>
      </div>
    </div>
  );
}