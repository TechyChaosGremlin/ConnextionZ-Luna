import { useState } from "react";
import { motion } from "motion/react";
import { Check, X, Users, DollarSign, Clock, MapPin } from "lucide-react";
import { useTheme } from "../ThemeContext";
import type { CollabRequest } from "../seed/types";

interface RequestCardProps {
  req: CollabRequest;
  onAccept: () => void;
  onDecline: () => void;
}

export function RequestCard({ req, onAccept, onDecline }: RequestCardProps) {
  const isDark = useTheme();
  const [expanded, setExpanded] = useState(false);

  const cardBg =        isDark ? "rgba(0,30,70,0.5)" : "rgba(255,255,255,0.85)";
  const usernameColor = isDark ? "#fff" : "#0a0e1a";
  const messageColor =  isDark ? "rgba(255,255,255,0.7)" : "rgba(10,14,26,0.65)";
  const metaBg =        isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.04)";
  const metaBorder =    isDark ? "1px solid rgba(255,255,255,0.1)" : "1px solid rgba(0,0,0,0.08)";
  const metaColor =     isDark ? "rgba(255,255,255,0.6)" : "rgba(10,14,26,0.55)";
  const declineBg =     isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.04)";
  const declineBorder = isDark ? "1px solid rgba(255,255,255,0.08)" : "1px solid rgba(0,0,0,0.08)";
  const declineColor =  isDark ? "rgba(255,255,255,0.5)" : "rgba(10,14,26,0.4)";
  const mutualColor =   isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)";
  const timeColor =     isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.35)";

  return (
    <motion.div layout
      initial=   {{ opacity: 0, y: 20               }} 
      animate=   {{ opacity: 1, y: 0                }} 
      exit=      {{ opacity: 0, x: -60, scale: 0.95 }}
      transition={{ duration: 0.25                  }}
      className="rounded-3xl overflow-hidden"
      style={{ background: cardBg, border: `1px solid ${req.accent}30` }}
    >
      <div className="h-0.5 w-full" style={{ background: `linear-gradient(90deg, ${req.accent}, transparent)` }} />

      <div className="p-4">
        <div className="flex items-start gap-3 mb-3">
          <div className="relative flex-shrink-0">
            <img src={req.avatar} alt={req.username} className="w-12 h-12 rounded-full object-cover border-2" style={{ borderColor: req.accent }} />
            {req.verified && (
              <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center" style={{ background: "#00AEEF" }}>
                <Check className="w-3 h-3 text-white" strokeWidth={3} />
              </div>
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-[15px]" style={{ color: usernameColor }}>@{req.username}</span>
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: `${req.accent}20`, color: req.accent, border: `1px solid ${req.accent}40` }}>
                {req.categoryIcon} {req.category}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-0.5">
              <span className="text-[12px] font-bold" style={{ color: "#00AEEF" }}>⭐ {req.collabScore}</span>
              {req.mutualCollabs > 0 && <span className="text-[11px]" style={{ color: mutualColor }}><Users className="w-3 h-3 inline mr-0.5" />{req.mutualCollabs} mutual</span>}
              <span className="text-[11px]" style={{ color: timeColor }}>{req.timeSent}</span>
            </div>
          </div>
        </div>

        <p className={`text-[13px] leading-relaxed mb-3 ${expanded ? "" : "line-clamp-2"}`} style={{ color: messageColor }}>
          "{req.message}"
        </p>
        {req.message.length > 100 && (
          <button onClick={() => setExpanded((e) => !e)} className="text-[12px] font-semibold mb-3" style={{ color: "#00AEEF" }}>
            {expanded ? "Show less" : "Read more"}
          </button>
        )}

        <div className="flex flex-wrap gap-2 mb-4">
          {req.budget && (
            <div className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold" style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.25)", color: isDark ? "#4ade80" : "#16a34a" }}>
              <DollarSign className="w-3 h-3" />{req.budget}
            </div>
          )}
          <div className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold" style={{ background: metaBg, border: metaBorder, color: metaColor }}>
            <Clock className="w-3 h-3" />{req.timeline}
          </div>
          <div className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold" style={{ background: metaBg, border: metaBorder, color: metaColor }}>
            <MapPin className="w-3 h-3" />{req.isRemote ? "Remote" : "In Person"}
          </div>
        </div>

        <div className="flex gap-2">
          <motion.button whileTap={{ scale: 0.96 }} onClick={onDecline}
            className="flex-1 py-3 rounded-2xl font-semibold text-[14px] flex items-center justify-center gap-1.5"
            style={{ background: declineBg, border: declineBorder, color: declineColor }}>
            <X className="w-4 h-4" /> Decline
          </motion.button>
          <motion.button whileTap={{ scale: 0.96 }} onClick={onAccept}
            className="flex-[2] py-3 rounded-2xl font-bold text-[14px] text-white flex items-center justify-center gap-1.5"
            style={{ background: `linear-gradient(135deg, ${req.accent}, ${req.accent}bb)`, boxShadow: `0 6px 20px ${req.accent}40` }}>
            <Check className="w-4 h-4" /> Accept Collab
          </motion.button>
        </div>
      </div>
    </motion.div>
  );
}
