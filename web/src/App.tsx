import { BookOpen, FileCode2, Home, Moon, Network, PanelLeft, Sun, SunMoon, Wifi, WifiOff, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { useStore, type Tab, type Theme } from "./lib/store";
import { useReconnect, useSocketStatus, useTopic } from "./lib/ws";
import Sidebar from "./components/Sidebar";
import { Toasts } from "./components/ui";
import HomeView from "./views/Home";
import LabView from "./views/LabView";
import ProfileView from "./views/ProfileView";
import HelpView from "./views/Help";

function TabIcon({ tab }: { tab: Tab }) {
  const labs = useStore((s) => s.labs);
  if (tab.kind === "home") return <Home size={14} />;
  if (tab.kind === "help") return <BookOpen size={14} />;
  if (tab.kind === "profile") return <FileCode2 size={14} />;
  const state = labs.find((l) => l.name === tab.name)?.state ?? "stopped";
  return state === "stopped" ? <Network size={14} /> : <span className={`dot ${state}`} />;
}

function tabTitle(tab: Tab) {
  if (tab.kind === "home") return "Overview";
  if (tab.kind === "help") return "Guide";
  return tab.name!;
}

const THEMES: { t: Theme; icon: typeof Sun; label: string }[] = [
  { t: "light", icon: Sun, label: "Light" },
  { t: "dark", icon: Moon, label: "Dark" },
  { t: "system", icon: SunMoon, label: "System" },
];

export default function App() {
  const { tabs, active, setActive, closeTab, theme, setTheme, dirty } = useStore();
  const refreshLabs = useStore((s) => s.refreshLabs);
  const refreshProfiles = useStore((s) => s.refreshProfiles);
  const setLabs = useStore((s) => s.setLabs);
  const status = useSocketStatus();
  const [sidebar, setSidebar] = useState(() => localStorage.getItem("netsim.sidebar") !== "0");
  const [authError, setAuthError] = useState(false);

  useEffect(() => {
    api
      .get<{ user: string; home: string; version: string }>("/api/info")
      .then((info) => useStore.setState({ info }))
      .catch((e) => e.status === 401 && setAuthError(true));
    refreshLabs().catch(() => {});
    refreshProfiles().catch(() => {});
  }, [refreshLabs, refreshProfiles]);

  useTopic("labs", (d) => setLabs(d.labs));
  useTopic("profiles", () => refreshProfiles());
  useReconnect(() => {
    refreshLabs().catch(() => {});
    refreshProfiles().catch(() => {});
  });

  const current = tabs.find((t) => t.id === active) ?? tabs[0];
  const nextTheme = THEMES[(THEMES.findIndex((x) => x.t === theme) + 1) % THEMES.length];
  const ThemeIcon = THEMES.find((x) => x.t === theme)!.icon;

  if (authError) {
    return (
      <div className="empty" style={{ height: "100%" }}>
        <div className="col" style={{ alignItems: "center" }}>
          <h2>Not authorized</h2>
          <p className="muted">
            Open the link printed by <code>netsim serve</code>, or run <code>netsim ui</code>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <button className="icon-btn sm" title="Toggle sidebar" onClick={() => {
            localStorage.setItem("netsim.sidebar", sidebar ? "0" : "1");
            setSidebar(!sidebar);
          }}>
            <PanelLeft size={15} />
          </button>
          <img src="/favicon.svg" alt="" />
          netsim <span className="ver">{useStore.getState().info?.version}</span>
        </div>
        <nav className="tabs">
          {tabs.map((t) => (
            <div
              key={t.id}
              className={`tab ${t.id === current.id ? "active" : ""} ${dirty[t.id] ? "dirty" : ""}`}
              onClick={() => setActive(t.id)}
              onMouseDown={(e) => e.button === 1 && (e.preventDefault(), closeTab(t.id))}
              title={t.name}
            >
              <TabIcon tab={t} />
              <span className="label">{tabTitle(t)}</span>
              <button
                className="close"
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(t.id);
                }}
              >
                <X size={12} className="x" />
              </button>
            </div>
          ))}
        </nav>
        <div className="topbar-right">
          <span className={`badge ${status === "open" ? "ok" : "warn"}`} title="live connection to the daemon">
            {status === "open" ? <Wifi size={12} /> : <WifiOff size={12} />}
            {status === "open" ? "live" : status}
          </span>
          <button className="icon-btn" title={`Theme: ${theme} (click for ${nextTheme.label})`} onClick={() => setTheme(nextTheme.t)}>
            <ThemeIcon size={16} />
          </button>
        </div>
      </header>
      <div className={`body ${sidebar ? "" : "nosidebar"}`}>
        {sidebar ? <Sidebar /> : <div />}
        <main className="main">
          {tabs.map((t) => (
            <div key={t.id} style={{ display: t.id === current.id ? "flex" : "none", flex: 1, minHeight: 0, flexDirection: "column" }}>
              {t.kind === "home" && <HomeView />}
              {t.kind === "help" && <HelpView />}
              {t.kind === "lab" && <LabView name={t.name!} tabId={t.id} visible={t.id === current.id} />}
              {t.kind === "profile" && <ProfileView name={t.name!} tabId={t.id} />}
            </div>
          ))}
        </main>
      </div>
      <Toasts />
    </div>
  );
}
