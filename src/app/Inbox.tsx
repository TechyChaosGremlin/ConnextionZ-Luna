import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ArrowLeft, Zap } from "lucide-react";
import { useTheme } from "./ThemeContext";
import { SEED_REQUESTS, SEED_CONVOS } from "./seed/data";
import type { CollabRequest, Conversation } from "./seed/types";
import { RequestCard } from "./components/RequestCard";
import { DMThread } from "./components/DMThread";
import { CelebrationOverlay } from "./components/CelebrationOverlay";

export function InboxScreen({ onBack }: { onBack: () => void }) {
  const isDark = useTheme();
  const [tab, setTab] = useState<"messages" | "requests">("requests");
  const [requests, setRequests] = useState<CollabRequest[]>(SEED_REQUESTS);
  const [convos] = useState<Conversation[]>(SEED_CONVOS);
  const [celebratingUser, setCelebratingUser] = useState<string | null>(null);
  const [openConvo, setOpenConvo] = useState<Conversation | null>(null);

  const totalUnread = convos.reduce((n, c) => n + c.unread, 0);

  const acceptRequest = (req: CollabRequest) => {
    setRequests((prev) => prev.filter((r) => r.id !== req.id));
    setCelebratingUser(req.username);
  };

  const declineRequest = (id: string) => {
    setRequests((prev) => prev.filter((r) => r.id !== id));
  };

  const D = {
    bg: isDark ? "linear-gradient(160deg,#00071a,#000c22)" : "linear-gradient(160deg,#f2f5fb,#eaf1fc)",
    headerBorder: isDark ? "rgba(0,174,239,0.1)" : "rgba(0,0,0,0.08)",
    backBg: isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.05)",
    backIcon: isDark ? "#fff" : "#0a0e1a",
    heading: isDark ? "#fff" : "#0a0e1a",
    tabActive: isDark ? "#fff" : "#0a0e1a",
    tabInactive: isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.4)",
    tabBorder: isDark ? "rgba(0,174,239,0.08)" : "rgba(0,0,0,0.07)",
    rowBorder: isDark ? "rgba(0,174,239,0.07)" : "rgba(0,0,0,0.06)",
    usernameColor: isDark ? "#fff" : "#0a0e1a",
    timestampColor: isDark ? "rgba(255,255,255,0.35)" : "rgba(10,14,26,0.4)",
    unreadMsg: isDark ? "rgba(255,255,255,0.8)" : "rgba(10,14,26,0.85)",
    readMsg: isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)",
    emptyHeading: isDark ? "#fff" : "#0a0e1a",
    emptySubtext: isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)",
    sectionLabel: isDark ? "rgba(255,255,255,0.4)" : "rgba(10,14,26,0.4)",
  };

  return (
    <div className="absolute inset-0 z-20 flex flex-col" style={{ background: D.bg }}>
      {/* Header */}
      <div className="flex items-center gap-3 px-5 pt-14 pb-4" style={{ borderBottom: `1px solid ${D.headerBorder}` }}>
        <button onClick={onBack} className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: D.backBg }}>
          <ArrowLeft className="w-4 h-4" style={{ color: D.backIcon }} />
        </button>
        <h1 className="font-extrabold text-[22px] flex-1" style={{ color: D.heading }}>Inbox</h1>
        {requests.length > 0 && (
          <div className="px-2.5 py-0.5 rounded-full text-[11px] font-bold" style={{ background: "#00AEEF", color: "#000" }}>
            {requests.length}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex px-5 pt-3 pb-1 gap-5" style={{ borderBottom: `1px solid ${D.tabBorder}` }}>
        {(["requests", "messages"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className="flex items-center gap-2 pb-3 relative font-semibold text-[14px] capitalize transition-colors"
            style={{ color: tab === t ? D.tabActive : D.tabInactive }}>
            {t === "requests" ? "Collab Requests" : "Messages"}
            {t === "messages" && totalUnread > 0 && (
              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold" style={{ background: "#ef4444", color: "#fff" }}>{totalUnread}</span>
            )}
            {t === "requests" && requests.length > 0 && (
              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold" style={{ background: "#00AEEF", color: "#000" }}>{requests.length}</span>
            )}
            {tab === t && (
              <motion.div layoutId="inbox-tab" className="absolute bottom-0 inset-x-0 h-0.5 rounded-full" style={{ background: "#00AEEF" }} />
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto" style={{ scrollbarWidth: "none" }}>
        <AnimatePresence mode="wait">
          {tab === "requests" && (
            <motion.div key="reqs" initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }}
              className="px-4 py-4 space-y-4 pb-10">
              {requests.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 gap-4 text-center">
                  <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: "rgba(0,174,239,0.1)", border: "1px solid rgba(0,174,239,0.2)" }}>
                    <Zap className="w-7 h-7" style={{ color: "#00AEEF" }} />
                  </div>
                  <div>
                    <p className="font-bold text-[17px]" style={{ color: D.emptyHeading }}>All caught up!</p>
                    <p className="text-[13px] mt-1" style={{ color: D.emptySubtext }}>New collab requests will appear here</p>
                  </div>
                </div>
              ) : (
                <>
                  <p className="text-[12px] font-semibold uppercase tracking-widest" style={{ color: D.sectionLabel }}>{requests.length} pending request{requests.length !== 1 ? "s" : ""}</p>
                  <AnimatePresence>
                    {requests.map((req) => (
                      <RequestCard key={req.id} req={req} onAccept={() => acceptRequest(req)} onDecline={() => declineRequest(req.id)} />
                    ))}
                  </AnimatePresence>
                </>
              )}
            </motion.div>
          )}

          {tab === "messages" && (
            <motion.div key="msgs" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}
              className="pb-10">
              {convos.map((convo, i) => (
                <motion.button key={convo.id} whileTap={{ scale: 0.98 }} onClick={() => setOpenConvo(convo)}
                  className="w-full flex items-center gap-3 px-5 py-4 text-left active:opacity-80"
                  style={{ borderBottom: i < convos.length - 1 ? `1px solid ${D.rowBorder}` : "none" }}>
                  <div className="relative flex-shrink-0">
                    <img src={convo.avatar} alt={convo.username} className="w-12 h-12 rounded-full object-cover" />
                    {convo.online && <div className="absolute bottom-0 right-0 w-3 h-3 rounded-full bg-green-400 border-2" style={{ borderColor: isDark ? "#000" : "#f2f5fb" }} />}
                    {convo.hasCollabBadge && (
                      <div className="absolute -top-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center text-white font-extrabold text-[9px]"
                        style={{ background: "linear-gradient(135deg,#00AEEF,#0077cc)", boxShadow: "0 2px 8px rgba(0,174,239,0.5)" }}>C</div>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-0.5">
                      <span className="font-semibold text-[14px]" style={{ color: D.usernameColor }}>@{convo.username}</span>
                      <span className="text-[11px]" style={{ color: D.timestampColor }}>{convo.timestamp}</span>
                    </div>
                    <p className="text-[13px] truncate" style={{ color: convo.unread > 0 ? D.unreadMsg : D.readMsg, fontWeight: convo.unread > 0 ? 600 : 400 }}>
                      {convo.lastMsg}
                    </p>
                  </div>
                  {convo.unread > 0 && (
                    <div className="w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0" style={{ background: "#00AEEF", color: "#000" }}>
                      {convo.unread}
                    </div>
                  )}
                </motion.button>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* DM Thread */}
      <AnimatePresence>
        {openConvo && (
          <DMThread key={openConvo.id} convo={openConvo} onBack={() => setOpenConvo(null)} />
        )}
      </AnimatePresence>

      {/* Celebration overlay */}
      <AnimatePresence>
        {celebratingUser && (
          <CelebrationOverlay key="celeb" username={celebratingUser} onDone={() => setCelebratingUser(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}

