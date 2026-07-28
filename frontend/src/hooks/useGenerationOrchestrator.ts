import { Dispatch, SetStateAction, useRef } from "react";
import toast from "react-hot-toast";
import { nanoid } from "nanoid";
import { generateCode } from "../generateCode";
import { AppState, DesignSystem, Settings } from "../types";
import { USER_CLOSE_WEB_SOCKET_CODE } from "../constants";
import { Stack } from "../lib/stacks";
import { CodeGenerationModel } from "../lib/models";
import {
  buildAssistantHistoryMessage,
  buildUpdateGenerationRequest,
  buildUserHistoryMessage,
  cloneVariantHistory,
  GenerationRequest,
  registerAssetIds,
} from "../lib/prompt-history";
import { useAppStore } from "../store/app-store";
import { useProjectStore } from "../store/project-store";
import {
  buildSelectedElementInstruction,
  describeElementContext,
} from "../components/select-and-edit/utils";
import { AiEditCommit, Commit } from "../components/commits/types";
import { createCommit } from "../components/commits/utils";

interface UseGenerationOrchestratorArgs {
  settings: Settings;
  setSettings: Dispatch<SetStateAction<Settings>>;
  designSystems: DesignSystem[];
}

// Owns the full generation lifecycle: websocket wiring, commit/variant state
// transitions, and the create/update/regenerate/import entry points that the
// UI panes call into. Extracted from App.tsx so the component tree only deals
// with layout and settings.
export function useGenerationOrchestrator({
  settings,
  setSettings,
  designSystems,
}: UseGenerationOrchestratorArgs) {
  const {
    // Inputs
    inputMode,
    setInputMode,
    referenceImages,
    setReferenceImages,
    initialPrompt,
    setInitialPrompt,
    upsertPromptAssets,
    resetPromptAssets,

    head,
    commits,
    addCommit,
    removeCommit,
    setHead,
    appendCommitCode,
    setCommitCode,
    resetCommits,
    resetHead,
    updateVariantStatus,
    resizeVariants,
    setVariantModels,
    setVariantScore,
    setVariantUsage,
    appendVariantHistoryMessage,
    startAgentEvent,
    appendAgentEventContent,
    finishAgentEvent,

    // Outputs
    appendExecutionConsole,
    resetExecutionConsoles,
  } = useProjectStore();

  const {
    disableInSelectAndEditMode,
    setUpdateInstruction,
    updateImages,
    setUpdateImages,
    setAppState,
    selectedElement,
    setSelectedElement,
  } = useAppStore();

  const wsRef = useRef<WebSocket>(null);
  const lastThinkingEventIdRef = useRef<Record<number, string>>({});
  const lastAssistantEventIdRef = useRef<Record<number, string>>({});
  const lastToolEventIdRef = useRef<Record<number, string>>({});

  const getAssetsById = () => useProjectStore.getState().assetsById;

  const reset = () => {
    // Stop any in-flight generation so late websocket events can't mutate
    // state after the reset (e.g. flipping the app back to CODE_READY).
    cancelCodeGeneration();
    setAppState(AppState.INITIAL);
    setUpdateInstruction("");
    setUpdateImages([]);
    disableInSelectAndEditMode();
    resetExecutionConsoles();

    resetCommits();
    resetHead();
    resetPromptAssets();

    // Inputs
    setInputMode("image");
    setReferenceImages([]);
  };

  const regenerate = () => {
    if (head === null) {
      toast.error(
        "No current version set. Please contact support via chat or Github."
      );
      throw new Error("Regenerate called with no head");
    }

    const currentCommit = commits[head];
    if (!currentCommit) {
      toast.error("The selected version could not be found.");
      return;
    }

    if (currentCommit.type === "ai_edit") {
      regenerateUpdate(currentCommit);
      return;
    }

    if (currentCommit.type === "code_create") {
      toast.error("Imported code cannot be regenerated.");
      return;
    }

    // Re-run the initial create request.
    if (inputMode === "image" || inputMode === "video") {
      doCreate(referenceImages, inputMode);
    } else {
      doCreateFromText(initialPrompt);
    }
  };

  // Used when the user cancels the code generation
  const cancelCodeGeneration = () => {
    wsRef.current?.close?.(USER_CLOSE_WEB_SOCKET_CODE);
  };

  // Used for user-initiated cancellation and failed edit rollbacks
  const cancelCodeGenerationAndReset = (commit: Commit) => {
    // When the current commit is the first version, reset the entire app state
    if (commit.type === "ai_create") {
      reset();
    } else {
      // Otherwise, remove current commit from commits
      removeCommit(commit.hash);

      // Revert to parent commit
      const parentCommitHash = commit.parentHash;
      if (parentCommitHash) {
        setHead(parentCommitHash);
      } else {
        throw new Error("Parent commit not found");
      }

      setAppState(AppState.CODE_READY);
    }
  };

  function doGenerateCode(
    params: GenerationRequest,
    generationParentHash: string | null = head
  ) {
    // Reset the execution console
    resetExecutionConsoles();

    // Set the app state to coding during generation
    setAppState(AppState.CODING);

    const { variantHistory, ...requestParams } = params;

    const selectedDesignSystem = designSystems.find(
      (designSystem) => designSystem.id === settings.selectedDesignSystemId
    );

    // Merge settings with params
    const updatedParams = {
      ...settings,
      ...requestParams,
      designSystem: selectedDesignSystem?.content ?? null,
    };

    // Settings-driven credentials: when the user added an enabled provider but
    // left the matching manual API key field empty, use the provider's
    // credentials so .env on the backend is unnecessary.
    if (!settings.openAiApiKey) {
      const defaultProvider = settings.providers.find(
        (p) => p.enabled && p.family === "openai"
      );
      if (defaultProvider) {
        updatedParams.openAiApiKey = defaultProvider.apiKey;
        if (defaultProvider.baseUrl) {
          updatedParams.openAiBaseURL = defaultProvider.baseUrl;
        }
      }
    }

    // If an OpenAI proxy / OmniRoute base URL is active and the user hasn't explicitly
    // selected a custom model, default the code generation model to OmniRoute Gemini 3.6 Flash.
    const currentModel = settings.codeGenerationModel || "";
    if (
      updatedParams.openAiBaseURL &&
      (!currentModel ||
        currentModel.startsWith("gpt-5") ||
        currentModel.startsWith("gemini-3"))
    ) {
      updatedParams.codeGenerationModel =
        CodeGenerationModel.OMNIR_GEMINI_3_6_FLASH_HIGH;
    }
    if (!settings.anthropicApiKey) {
      const defaultProvider = settings.providers.find(
        (p) => p.enabled && p.family === "anthropic"
      );
      if (defaultProvider) {
        updatedParams.anthropicApiKey = defaultProvider.apiKey;
      }
    }
    if (!settings.geminiApiKey) {
      const defaultProvider = settings.providers.find(
        (p) => p.enabled && p.family === "gemini"
      );
      if (defaultProvider) {
        updatedParams.geminiApiKey = defaultProvider.apiKey;
      }
    }

    // Use the Variant Builder count when configured; otherwise 4 variants
    // for create and 2 for edits to match backend counts and avoid a flash
    // when the backend sends the actual variant count
    const initialVariantCount =
      settings.variantModelConfigs && settings.variantModelConfigs.length > 0
        ? settings.variantModelConfigs.length
        : requestParams.generationType === "create"
        ? 4
        : 2;
    const baseCommitObject = {
      variants: Array(initialVariantCount)
        .fill(null)
        .map(() => ({
          code: "",
          history: cloneVariantHistory(variantHistory),
        })),
    };

    const commitInputObject =
      requestParams.generationType === "create"
        ? {
            ...baseCommitObject,
            type: "ai_create" as const,
            parentHash: null,
            inputs: requestParams.prompt,
          }
        : {
            ...baseCommitObject,
            type: "ai_edit" as const,
            parentHash: generationParentHash,
            inputs: requestParams.prompt,
          };

    // Create a new commit and set it as the head
    const commit = createCommit(commitInputObject);
    addCommit(commit);
    setHead(commit.hash);

    lastThinkingEventIdRef.current = {};
    lastAssistantEventIdRef.current = {};
    lastToolEventIdRef.current = {};

    const finishThinkingEvent = (variantIndex: number, status: "complete" | "error") => {
      const eventId = lastThinkingEventIdRef.current[variantIndex];
      if (!eventId) return;
      finishAgentEvent(commit.hash, variantIndex, eventId, {
        status,
        endedAt: Date.now(),
      });
      delete lastThinkingEventIdRef.current[variantIndex];
    };

    const finishAssistantEvent = (variantIndex: number, status: "complete" | "error") => {
      const eventId = lastAssistantEventIdRef.current[variantIndex];
      if (!eventId) return;
      finishAgentEvent(commit.hash, variantIndex, eventId, {
        status,
        endedAt: Date.now(),
      });
      delete lastAssistantEventIdRef.current[variantIndex];
    };

    const finishToolEvent = (variantIndex: number, status: "complete" | "error") => {
      const eventId = lastToolEventIdRef.current[variantIndex];
      if (!eventId) return;
      finishAgentEvent(commit.hash, variantIndex, eventId, {
        status,
        endedAt: Date.now(),
      });
      delete lastToolEventIdRef.current[variantIndex];
    };

    const finishInFlightEvents = (status: "complete" | "error") => {
      Object.keys(lastThinkingEventIdRef.current).forEach((key) => {
        finishThinkingEvent(Number(key), status);
      });
      Object.keys(lastAssistantEventIdRef.current).forEach((key) => {
        finishAssistantEvent(Number(key), status);
      });
      Object.keys(lastToolEventIdRef.current).forEach((key) => {
        finishToolEvent(Number(key), status);
      });
    };

    generateCode(wsRef, updatedParams, {
      onChange: (token, variantIndex) => {
        appendCommitCode(commit.hash, variantIndex, token);
      },
      onSetCode: (code, variantIndex) => {
        setCommitCode(commit.hash, variantIndex, code);
      },
      onStatusUpdate: (line, variantIndex) =>
        appendExecutionConsole(variantIndex, line),
      onVariantComplete: (variantIndex, usage) => {
        console.log(`Variant ${variantIndex} complete event received`);
        updateVariantStatus(commit.hash, variantIndex, "complete");
        if (usage) {
          setVariantUsage(commit.hash, variantIndex, usage);
        }
        const currentCode =
          useProjectStore.getState().commits[commit.hash]?.variants[variantIndex]
            ?.code || "";
        if (currentCode.trim().length > 0) {
          appendVariantHistoryMessage(
            commit.hash,
            variantIndex,
            buildAssistantHistoryMessage(currentCode)
          );
        }
        finishThinkingEvent(variantIndex, "complete");
        finishAssistantEvent(variantIndex, "complete");
        finishToolEvent(variantIndex, "complete");
        if (commit.type === "ai_edit") {
          const {
            updateInstruction: currentInstruction,
            updateImages: currentImages,
          } = useAppStore.getState();
          const instructionUnchanged =
            currentInstruction === commit.inputs.text;
          const imagesUnchanged =
            currentImages.length === commit.inputs.images.length &&
            currentImages.every(
              (image, index) => image === commit.inputs.images[index]
            );

          // This conditional clear handles three UX scenarios:
          // 1) All variants fail: no completion event, so keep prompt/images for retry.
          // 2) A variant completes and user has typed/changed images: do not clear.
          // 3) A variant completes and user has not changed draft: clear for next edit.
          if (instructionUnchanged && imagesUnchanged) {
            setUpdateInstruction("");
            setUpdateImages([]);
          }
        }
      },
      onVariantError: (variantIndex, error) => {
        console.error(`Error in variant ${variantIndex}:`, error);
        updateVariantStatus(commit.hash, variantIndex, "error", error);
        finishThinkingEvent(variantIndex, "error");
        finishAssistantEvent(variantIndex, "error");
        finishToolEvent(variantIndex, "error");
      },
      onVariantCount: (count) => {
        console.log(`Backend is using ${count} variants`);
        resizeVariants(commit.hash, count);
      },
      onVariantModels: (models) => {
        setVariantModels(commit.hash, models);
      },
      onVariantScore: (variantIndex, score) => {
        setVariantScore(commit.hash, variantIndex, score);
      },
      onThinking: (content, variantIndex, eventId) => {
        if (!eventId) return;
        lastThinkingEventIdRef.current[variantIndex] = eventId;
        startAgentEvent(commit.hash, variantIndex, {
          id: eventId,
          type: "thinking",
          status: "running",
          startedAt: Date.now(),
        });
        appendAgentEventContent(commit.hash, variantIndex, eventId, content);
      },
      onAssistant: (content, variantIndex, eventId) => {
        if (!eventId) return;
        lastAssistantEventIdRef.current[variantIndex] = eventId;
        startAgentEvent(commit.hash, variantIndex, {
          id: eventId,
          type: "assistant",
          status: "running",
          startedAt: Date.now(),
        });
        appendAgentEventContent(commit.hash, variantIndex, eventId, content);
      },
      onToolStart: (data, variantIndex, eventId) => {
        if (!eventId) return;
        const lastThinking = lastThinkingEventIdRef.current[variantIndex];
        if (lastThinking && lastThinking !== eventId) {
          finishThinkingEvent(variantIndex, "complete");
        }
        const lastAssistant = lastAssistantEventIdRef.current[variantIndex];
        if (lastAssistant && lastAssistant !== eventId) {
          finishAssistantEvent(variantIndex, "complete");
        }
        startAgentEvent(commit.hash, variantIndex, {
          id: eventId,
          type: "tool",
          status: "running",
          toolName: data?.name,
          input: data?.input,
          startedAt: Date.now(),
        });
        lastToolEventIdRef.current[variantIndex] = eventId;
      },
      onToolResult: (data, variantIndex, eventId) => {
        if (!eventId) return;
        finishAgentEvent(commit.hash, variantIndex, eventId, {
          status: data?.ok === false ? "error" : "complete",
          output: data?.output,
          endedAt: Date.now(),
        });
        if (lastToolEventIdRef.current[variantIndex] === eventId) {
          delete lastToolEventIdRef.current[variantIndex];
        }
      },
      onCancel: (reason, errorMessage) => {
        // The project may have been reset while this generation was still in
        // flight — a stale cancellation must not mutate app state.
        if (!useProjectStore.getState().commits[commit.hash]) return;

        // Close any running agent events when the socket ends without per-event
        // terminal messages, otherwise they remain stuck in "running" state.
        finishInFlightEvents(reason === "request_failed" ? "error" : "complete");

        if (reason === "request_failed" && commit.type === "ai_create") {
          const latestCreateCommit = useProjectStore.getState().commits[commit.hash];
          latestCreateCommit?.variants.forEach((variant, variantIndex) => {
            if (variant.status === "generating") {
              updateVariantStatus(
                commit.hash,
                variantIndex,
                "error",
                errorMessage || "Generation failed. Please retry."
              );
            }
          });
          setAppState(AppState.CODE_READY);
          return;
        }

        cancelCodeGenerationAndReset(commit);
      },
      onComplete: () => {
        // Same guard as onCancel: a generation finishing after its project
        // was reset must not pull the app back into the editor.
        if (!useProjectStore.getState().commits[commit.hash]) return;
        finishInFlightEvents("complete");
        setAppState(AppState.CODE_READY);
      },
    });
  }

  // Initial version creation
  function doCreate(
    referenceImages: string[],
    inputMode: "image" | "video",
    textPrompt: string = "",
    isAssetExtractionEnabled = true
  ) {
    // Reset any existing state
    reset();

    // Set the input states
    setReferenceImages(referenceImages);
    setInputMode(inputMode);

    // Kick off the code generation
    if (referenceImages.length > 0) {
      const media =
        inputMode === "video" ? [referenceImages[0]] : referenceImages;
      const imageAssetIds =
        inputMode === "image"
          ? registerAssetIds(
              "image",
              media,
              getAssetsById,
              upsertPromptAssets,
              nanoid
            )
          : [];
      const videoAssetIds =
        inputMode === "video"
          ? registerAssetIds(
              "video",
              media,
              getAssetsById,
              upsertPromptAssets,
              nanoid
            )
          : [];
      const variantHistory = [
        buildUserHistoryMessage(textPrompt, imageAssetIds, videoAssetIds),
      ];
      doGenerateCode({
        generationType: "create",
        inputMode,
        prompt: {
          text: textPrompt,
          images: inputMode === "image" ? media : [],
          videos: inputMode === "video" ? media : [],
        },
        // Asset extraction operates on still screenshots. Video data uses the
        // same transport shape for Gemini, so explicitly disable extraction
        // instead of letting the agent try to crop a video payload.
        isAssetExtractionEnabled:
          inputMode === "image" && isAssetExtractionEnabled,
        variantHistory,
      });
    }
  }

  function doCreateFromText(text: string) {
    // Reset any existing state
    reset();

    setInputMode("text");
    setInitialPrompt(text);
    doGenerateCode({
      generationType: "create",
      inputMode: "text",
      prompt: { text, images: [], videos: [] },
      variantHistory: [buildUserHistoryMessage(text)],
    });
  }

  function regenerateUpdate(commit: AiEditCommit) {
    const parentHash = commit.parentHash;
    const parentCommit = parentHash ? commits[parentHash] : null;
    if (!parentHash || !parentCommit) {
      toast.error("The previous version needed to retry this edit was not found.");
      return;
    }

    const parentVariant =
      parentCommit.variants[parentCommit.selectedVariantIndex];
    if (!parentVariant) {
      toast.error("The selected option from the previous version was not found.");
      return;
    }

    const imageAssetIds = registerAssetIds(
      "image",
      commit.inputs.images,
      getAssetsById,
      upsertPromptAssets,
      nanoid
    );

    doGenerateCode(
      buildUpdateGenerationRequest({
        inputMode,
        prompt: commit.inputs,
        parentCommit,
        imageAssetIds,
        getAssetsById,
      }),
      parentHash
    );
  }

  // Subsequent updates
  async function doUpdate(updateInstruction: string) {
    if (updateInstruction.trim() === "") {
      toast.error("Please include some instructions for AI on what to update.");
      return;
    }

    if (head === null) {
      toast.error(
        "No current version set. Contact support or open a Github issue."
      );
      throw new Error("Update called with no head");
    }

    const currentCommit = commits[head];
    if (!currentCommit) {
      toast.error("The selected version could not be found.");
      return;
    }

    let modifiedUpdateInstruction = updateInstruction;
    let selectedElementHtml: string | undefined;

    // Send in a reference to the selected element if it exists. Selection
    // visuals are overlays, so the element's outerHTML is already clean.
    if (selectedElement) {
      const elementHtml = selectedElement.outerHTML;
      selectedElementHtml = elementHtml;
      modifiedUpdateInstruction = buildSelectedElementInstruction(
        updateInstruction,
        elementHtml,
        selectedElement.isConnected
          ? describeElementContext(selectedElement)
          : undefined
      );
      setSelectedElement(null);
    }

    const updateImageAssetIds = registerAssetIds(
      "image",
      updateImages,
      getAssetsById,
      upsertPromptAssets,
      nanoid
    );

    doGenerateCode(
      buildUpdateGenerationRequest({
        inputMode,
        prompt: {
          text: updateInstruction,
          fullText: modifiedUpdateInstruction,
          images: updateImages,
          videos: [],
          selectedElementHtml,
        },
        parentCommit: currentCommit,
        imageAssetIds: updateImageAssetIds,
        getAssetsById,
      })
    );
  }

  function importFromCode(code: string, stack: Stack) {
    // Reset any existing state
    reset();

    // Set up this project
    setSettings((prev) => ({
      ...prev,
      generatedCodeConfig: stack,
    }));

    // Create a new commit and set it as the head
    const commit = createCommit({
      type: "code_create",
      parentHash: null,
      variants: [{ code, history: [] }],
      inputs: null,
    });
    addCommit(commit);
    setHead(commit.hash);

    // Set the app state
    setAppState(AppState.CODE_READY);
  }

  return {
    doCreate,
    doCreateFromText,
    doUpdate,
    regenerate,
    reset,
    cancelCodeGeneration,
    importFromCode,
  };
}
