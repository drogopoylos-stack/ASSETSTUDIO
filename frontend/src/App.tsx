import { Suspense, lazy, useEffect } from "react";
import ClaudeLoginModal from "./components/ClaudeLoginModal";
import StatusBar from "./components/StatusBar";
import Toasts from "./components/Toasts";
import TopNav from "./components/TopNav";
import UpdateBanner from "./components/UpdateBanner";
import { cls } from "./components/ui";
import { useTurnEndNotifier } from "./hooks/useTurnEndNotifier";
import { tabFromPath, useStore } from "./store/useStore";
import { Gauge, Loader2 } from "lucide-react";

// Pages are lazy-loaded so the window opens fast: only the visited tab's code is
// parsed (the eager bundle was one 5.4 MB chunk — every tab, paid up front). Vite
// splits each import() into its own cached chunk; switching tabs after the first
// visit is instant.
const PAGES: Record<string, React.LazyExoticComponent<React.FC>> = {
  dashboard: lazy(() => import("./pages/Dashboard")),
  mission: lazy(() => import("./pages/MissionControl")),
  workspace: lazy(() => import("./pages/Workspace")),
  workflows: lazy(() => import("./pages/Workflows")),
  chat: lazy(() => import("./pages/AskAI")),
  plans: lazy(() => import("./pages/Plans")),
  image: lazy(() => import("./pages/ImageStudio")),
  video: lazy(() => import("./pages/VideoStudio")),
  studio2d: lazy(() => import("./pages/Studio2D")),
  studio3d: lazy(() => import("./pages/Studio3D")),
  texture: lazy(() => import("./pages/Texture")),
  rig: lazy(() => import("./pages/Rig")),
  pipeline: lazy(() => import("./pages/Pipeline")),
  catalog: lazy(() => import("./pages/Catalog")),
  jobs: lazy(() => import("./pages/Jobs")),
  compare: lazy(() => import("./pages/Compare")),
  servers: lazy(() => import("./pages/Servers")),
  settings: lazy(() => import("./pages/Settings")),
};

function PageLoading() {
  return (
    <div className="h-full min-h-[40vh] flex items-center justify-center text-muted">
      <Loader2 size={18} className="animate-spin" />
    </div>
  );
}

// Its own window, not a tab: electron/main.cjs gives `/engine` its own geometry and its own name
// in the taskbar. Read once at module scope, because the branch below decides which HOOKS run —
// `useTurnEndNotifier` in a second window would fire every OS notification twice — and a value
// that could change between renders would make that hook order illegal.
const IS_ENGINE_WINDOW = typeof location !== "undefined"
  && location.pathname.replace(/^\/+/, "").split("/")[0] === "engine";

const EnginePage = lazy(() => import("./pages/Engine"));

function EngineWindow() {
  return (
    <div className="h-screen w-screen overflow-hidden bg-bg text-text">
      <Suspense fallback={<PageLoading />}>
        <EnginePage />
      </Suspense>
      <Toasts />
    </div>
  );
}

export default function App() {
  if (IS_ENGINE_WINDOW) return <EngineWindow />;
  return <MainApp />;
}

function MainApp() {
  const tab = useStore((s) => s.tab);
  const enabledTabs = useStore((s) => s.enabledTabs);
  const loadPlugins = useStore((s) => s.loadPlugins);
  // A disabled tab is never mounted, so Vite never fetches its chunk — that, not the
  // hidden button, is what turning a plugin off actually saves.
  const allowed = !enabledTabs || enabledTabs.includes(tab);
  const Page = (allowed && PAGES[tab]) || PAGES.workspace;
  const showStatusBar = useStore((s) => s.showStatusBar);
  const setShowStatusBar = useStore((s) => s.setShowStatusBar);
  // Mounted here, not in Workspace: a turn usually finishes while you are looking at some
  // other tab, and Workspace is unmounted then.
  useTurnEndNotifier();
  useEffect(() => {
    loadPlugins();
    const onPop = () => useStore.setState({ tab: tabFromPath() });
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [loadPlugins]);
  const effTab = allowed ? tab : "workspace";      // must match the page actually rendered
  const fullWidth = effTab === "mission"; // canvas tab uses the whole window
  // these manage their own full-window layout
  const fullBleed = effTab === "workspace" || effTab === "chat" || effTab === "workflows" || effTab === "plans" || effTab === "video";
  return (
    <div className="h-screen flex flex-col bg-bg text-text">
      <TopNav />
      <main className={cls("flex-1 min-h-0", fullBleed ? "overflow-hidden" : "overflow-y-auto")}>
        <Suspense fallback={<PageLoading />}>
          {fullBleed ? (
            <Page />
          ) : (
            <div className={fullWidth ? "px-3 py-4" : "max-w-[1500px] mx-auto p-4"}>
              <Page />
            </div>
          )}
        </Suspense>
      </main>
      {showStatusBar ? (
        <StatusBar />
      ) : (
        <button onClick={() => setShowStatusBar(true)} title="Show status bar (CPU / GPU / RAM)"
          className="fixed bottom-1.5 right-1.5 z-40 p-1.5 rounded-lg bg-panel border border-line text-muted hover:text-text shadow-card">
          <Gauge size={15} />
        </button>
      )}
      <UpdateBanner />
      <Toasts />
      <ClaudeLoginModal />
    </div>
  );
}
