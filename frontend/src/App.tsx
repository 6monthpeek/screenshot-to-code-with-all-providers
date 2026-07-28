import { useCallback, useEffect, useState } from "react";
import { AppState, AppTheme, EditorTheme, Settings } from "./types";
import { NEW_DESIGN_SYSTEM_CONTENT } from "./lib/design-systems";
import { IS_RUNNING_ON_CLOUD } from "./config";
import { OnboardingNote } from "./components/messages/OnboardingNote";
import { usePersistedState } from "./hooks/usePersistedState";
import TermsOfServiceDialog from "./components/TermsOfServiceDialog";
import toast from "react-hot-toast";
import { Stack } from "./lib/stacks";
import { CodeGenerationModel } from "./lib/models";
import useBrowserTabIndicator from "./hooks/useBrowserTabIndicator";
import { LuChevronLeft } from "react-icons/lu";
// import TipLink from "./components/messages/TipLink";
import { useAppStore } from "./store/app-store";
import { useDesignSystems } from "./hooks/useDesignSystems";
import { useGenerationOrchestrator } from "./hooks/useGenerationOrchestrator";
import { useEscapeToExitSelectMode } from "./components/select-and-edit/useEscapeToExitSelectMode";
import Sidebar from "./components/sidebar/Sidebar";
import IconStrip from "./components/sidebar/IconStrip";
import HistoryDisplay from "./components/history/HistoryDisplay";
import PreviewPane from "./components/preview/PreviewPane";
import StartPane from "./components/start-pane/StartPane";
import SettingsTab from "./components/settings/SettingsTab";
import DesignSystemsModal from "./components/settings/DesignSystemsModal";

function App() {
  const { appState } = useAppStore();

  // Settings
  const [settings, setSettings] = usePersistedState<Settings>(
    {
      openAiApiKey: null,
      openAiBaseURL: null,
      replicateApiKey: null,
      anthropicApiKey: null,
      geminiApiKey: null,
      screenshotOneApiKey: null,
      isImageGenerationEnabled: true,
      editorTheme: EditorTheme.COBALT,
      generatedCodeConfig: Stack.HTML_TAILWIND,
      codeGenerationModel: CodeGenerationModel.GEMINI_3_FLASH_PREVIEW_MINIMAL,
      selectedDesignSystemId: null,
      // Only relevant for hosted version
      isTermOfServiceAccepted: false,
      providers: [],
      variantModelConfigs: null,
    },
    "setting"
  );
  const [appTheme, setAppTheme] = usePersistedState<AppTheme>(
    AppTheme.SYSTEM,
    "app-theme"
  );

  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<"preview" | "chat">("preview");
  const [isDesignSystemsModalOpen, setIsDesignSystemsModalOpen] =
    useState(false);
  const [designSystemsModalInitialId, setDesignSystemsModalInitialId] =
    useState<string | null>(null);
  const {
    designSystems,
    isLoading: areDesignSystemsLoading,
    createDesignSystem,
    updateDesignSystem,
    deleteDesignSystem,
  } = useDesignSystems();

  const setSelectedDesignSystemId = useCallback(
    (id: string | null) => {
      setSettings((prev) => ({ ...prev, selectedDesignSystemId: id }));
    },
    [setSettings]
  );

  const openDesignSystemsManager = useCallback((focusedId?: string | null) => {
    setDesignSystemsModalInitialId(focusedId ?? null);
    setIsDesignSystemsModalOpen(true);
  }, []);

  const handleAddNewDesignSystem = useCallback(async () => {
    try {
      const isFirst = designSystems.length === 0;
      const created = await createDesignSystem({
        name: `Design system ${designSystems.length + 1}`,
        content: NEW_DESIGN_SYSTEM_CONTENT,
      });
      if (isFirst) {
        setSelectedDesignSystemId(created.id);
      }
      openDesignSystemsManager(created.id);
    } catch (error) {
      console.error("Failed to create design system", error);
      toast.error("Could not create design system.");
    }
  }, [
    createDesignSystem,
    designSystems.length,
    openDesignSystemsManager,
    setSelectedDesignSystemId,
  ]);

  // Generation lifecycle (WS wiring, commits/variants, create/update/import)
  const {
    doCreate,
    doCreateFromText,
    doUpdate,
    regenerate,
    reset,
    cancelCodeGeneration,
    importFromCode,
  } = useGenerationOrchestrator({ settings, setSettings, designSystems });

  // Indicate coding state using the browser tab's favicon and title
  useBrowserTabIndicator(appState === AppState.CODING);

  useEscapeToExitSelectMode();

  // When the user already has the settings in local storage, newly added keys
  // do not get added to the settings so if it's falsy, we populate it with the default
  // value
  useEffect(() => {
    if (!settings.generatedCodeConfig) {
      setSettings((prev) => ({
        ...prev,
        generatedCodeConfig: Stack.HTML_TAILWIND,
      }));
    }
  }, [settings.generatedCodeConfig, setSettings]);

  useEffect(() => {
    if (!("selectedDesignSystemId" in settings)) {
      setSettings((prev) => ({
        ...prev,
        selectedDesignSystemId: null,
      }));
    }
  }, [settings, setSettings]);


  useEffect(() => {
    if (
      settings.selectedDesignSystemId &&
      !areDesignSystemsLoading &&
      !designSystems.some(
        (designSystem) => designSystem.id === settings.selectedDesignSystemId
      )
    ) {
      setSettings((prev) => ({
        ...prev,
        selectedDesignSystemId: null,
      }));
    }
  }, [
    areDesignSystemsLoading,
    designSystems,
    settings.selectedDesignSystemId,
    setSettings,
  ]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const isDark =
        appTheme === AppTheme.DARK ||
        (appTheme === AppTheme.SYSTEM && mediaQuery.matches);
      document.documentElement.classList.toggle("dark", isDark);
      document.body.classList.toggle("dark", isDark);
    };

    applyTheme();

    if (appTheme !== AppTheme.SYSTEM) {
      return;
    }

    const onChange = () => applyTheme();
    mediaQuery.addEventListener("change", onChange);

    return () => {
      mediaQuery.removeEventListener("change", onChange);
    };
  }, [appTheme]);

  const handleTermDialogOpenChange = (open: boolean) => {
    setSettings((s) => ({
      ...s,
      isTermOfServiceAccepted: !open,
    }));
  };

  const showContentPanel =
    appState === AppState.CODING ||
    appState === AppState.CODE_READY ||
    isHistoryOpen;
  const isCodingOrReady =
    appState === AppState.CODING || appState === AppState.CODE_READY;
  const showMobileChatPane = showContentPanel && mobilePane === "chat";

  return (
    <div
      className={`dark:bg-black dark:text-white ${
        appState === AppState.CODING || appState === AppState.CODE_READY
          ? "flex h-dvh flex-col overflow-hidden lg:block lg:h-screen"
          : "min-h-screen"
      }`}
    >
      {IS_RUNNING_ON_CLOUD && (
        <TermsOfServiceDialog
          open={!settings.isTermOfServiceAccepted}
          onOpenChange={handleTermDialogOpenChange}
        />
      )}

      {/* Icon strip - always visible */}
      <div
        className="sticky top-0 z-50 lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:w-16 lg:flex-col"
      >
        <IconStrip
          isHistoryOpen={isHistoryOpen}
          isEditorOpen={!isHistoryOpen && !isSettingsOpen}
          isSettingsOpen={isSettingsOpen}
          showHistory={isCodingOrReady}
          showEditor={isCodingOrReady}
          onToggleHistory={() => {
            setIsHistoryOpen((prev) => !prev);
            setIsSettingsOpen(false);
            setMobilePane("chat");
          }}
          onToggleEditor={() => {
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
            setMobilePane("preview");
          }}
          onLogoClick={() => {
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
            setMobilePane("preview");
          }}
          onNewProject={() => {
            reset();
            setIsHistoryOpen(false);
            setIsSettingsOpen(false);
            setMobilePane("preview");
          }}
          onOpenSettings={() => {
            setIsSettingsOpen(true);
            setIsHistoryOpen(false);
          }}
        />
      </div>

      {isCodingOrReady && !isSettingsOpen && (
        <div className="border-b border-gray-200 bg-white px-4 py-2 dark:border-zinc-800 dark:bg-zinc-950 lg:hidden">
          <div className="grid grid-cols-2 rounded-xl bg-gray-100 p-1 dark:bg-zinc-800">
            <button
              onClick={() => {
                setIsHistoryOpen(false);
                setMobilePane("preview");
              }}
              className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                mobilePane === "preview"
                  ? "bg-white text-gray-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                  : "text-gray-500 dark:text-zinc-400"
              }`}
            >
              Preview
            </button>
            <button
              onClick={() => setMobilePane("chat")}
              className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                mobilePane === "chat"
                  ? "bg-white text-gray-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                  : "text-gray-500 dark:text-zinc-400"
              }`}
            >
              Chat
            </button>
          </div>
        </div>
      )}

      {/* Content panel - shows sidebar, history, or editor */}
      {showContentPanel && !isSettingsOpen && (
        <div
          className={`border-b border-gray-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 dark:text-white lg:fixed lg:inset-y-0 lg:left-16 lg:z-40 lg:flex lg:w-[calc(28rem-4rem)] lg:flex-col lg:border-b-0 lg:border-r ${
            showMobileChatPane ? "block" : "hidden lg:flex"
          }`}
        >
            {isHistoryOpen ? (
              <div className="flex-1 overflow-y-auto sidebar-scrollbar-stable px-4">
                <div className="mt-3">
                  <div className="flex items-center justify-between mb-3 px-1">
                    <h2 className="text-xs font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">Versions</h2>
                    <button
                      onClick={() => setIsHistoryOpen(false)}
                      className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
                    >
                      <LuChevronLeft className="w-3.5 h-3.5" />
                      Back to editor
                    </button>
                  </div>
                  <HistoryDisplay />
                </div>
              </div>
            ) : (
              <>
                {IS_RUNNING_ON_CLOUD && !settings.openAiApiKey && (
                  <div className="px-6 mt-4">
                    <OnboardingNote />
                  </div>
                )}

                {(appState === AppState.CODING ||
                  appState === AppState.CODE_READY) && (
                  <Sidebar
                    doUpdate={doUpdate}
                    regenerate={regenerate}
                    cancelCodeGeneration={cancelCodeGeneration}
                    designSystem={{
                      designSystems,
                      selectedDesignSystemId: settings.selectedDesignSystemId,
                      setSelectedDesignSystemId,
                      onAddNew: handleAddNewDesignSystem,
                      onManage: () => openDesignSystemsManager(),
                    }}
                    onOpenVersions={() => {
                      setIsHistoryOpen(true);
                      setMobilePane("chat");
                    }}
                  />
                )}
              </>
            )}
        </div>
      )}

      <main
        className={`${
          isSettingsOpen
            ? "flex flex-1 min-h-0 flex-col lg:h-full lg:pl-16"
            : showContentPanel
              ? "flex flex-1 min-h-0 flex-col lg:h-full lg:pl-[28rem]"
              : "lg:pl-16"
        } ${isCodingOrReady && !isSettingsOpen && mobilePane === "chat" ? "hidden lg:flex" : ""}`}
      >
        {isSettingsOpen ? (
          <SettingsTab
            settings={settings}
            setSettings={setSettings}
            appTheme={appTheme}
            setAppTheme={setAppTheme}
          />
        ) : (
          <>
            {appState === AppState.INITIAL && (
              <StartPane
                doCreate={doCreate}
                doCreateFromText={doCreateFromText}
                importFromCode={importFromCode}
                settings={settings}
                setSettings={setSettings}
                designSystems={designSystems}
                onAddNewDesignSystem={handleAddNewDesignSystem}
                onManageDesignSystems={() => openDesignSystemsManager()}
              />
            )}

            {isCodingOrReady && (
              <PreviewPane
                settings={settings}
                onOpenVersions={() => {
                  setIsHistoryOpen(true);
                  setMobilePane("chat");
                }}
              />
            )}
          </>
        )}
      </main>

      <DesignSystemsModal
        open={isDesignSystemsModalOpen}
        onOpenChange={setIsDesignSystemsModalOpen}
        designSystems={designSystems}
        selectedDesignSystemId={settings.selectedDesignSystemId}
        setSelectedDesignSystemId={setSelectedDesignSystemId}
        initialEditingId={designSystemsModalInitialId}
        createDesignSystem={createDesignSystem}
        updateDesignSystem={updateDesignSystem}
        deleteDesignSystem={deleteDesignSystem}
      />
    </div>
  );
}

export default App;
