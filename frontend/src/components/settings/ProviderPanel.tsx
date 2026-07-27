import { useState } from "react";
import { ProviderConfig, ProviderFamily } from "../../types";
import { Input } from "../ui/input";
import { Switch } from "../ui/switch";
import { Button } from "../ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";

interface Props {
  providers: ProviderConfig[];
  onChange: (providers: ProviderConfig[]) => void;
}

function newProviderId(): string {
  return `prov_${Math.random().toString(36).slice(2, 10)}`;
}

const FAMILY_LABELS: Record<ProviderFamily, string> = {
  openai: "OpenAI-compatible",
  anthropic: "Anthropic",
  gemini: "Gemini",
};

const FAMILY_DEFAULT_URL: Record<ProviderFamily, string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
  gemini: "https://generativelanguage.googleapis.com",
};

function ProviderPanel({ providers, onChange }: Props) {
  const [isAdding, setIsAdding] = useState(false);
  const [draft, setDraft] = useState<Partial<ProviderConfig>>({
    family: "openai",
    enabled: true,
  });

  const handleAdd = () => {
    if (!draft.label?.trim() || !draft.apiKey?.trim()) return;
    const newProvider: ProviderConfig = {
      id: newProviderId(),
      label: draft.label.trim(),
      family: (draft.family as ProviderFamily) || "openai",
      baseUrl: draft.baseUrl?.trim() || null,
      apiKey: draft.apiKey.trim(),
      enabled: draft.enabled ?? true,
    };
    onChange([...providers, newProvider]);
    setDraft({ family: "openai", enabled: true });
    setIsAdding(false);
  };

  const handleRemove = (id: string) => {
    onChange(providers.filter((p) => p.id !== id));
  };

  const handleToggle = (id: string) => {
    onChange(
      providers.map((p) => (p.id === id ? { ...p, enabled: !p.enabled } : p))
    );
  };

  const handleUpdate = (id: string, patch: Partial<ProviderConfig>) => {
    onChange(providers.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  };

  return (
    <div className="flex flex-col gap-y-3 border rounded-md p-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-sm">Providers</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Add LLM providers (OpenAI-compatible, Anthropic, Gemini). Each
            variant can use a different provider+model.
          </p>
        </div>
        {!isAdding && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setIsAdding(true)}
          >
            + Add provider
          </Button>
        )}
      </div>

      {providers.length === 0 && !isAdding && (
        <p className="text-xs text-gray-500 italic">
          No providers yet. Add one to unlock per-variant provider selection.
        </p>
      )}

      <ul className="flex flex-col gap-y-2">
        {providers.map((p) => (
          <li
            key={p.id}
            className="border rounded p-3 flex flex-col gap-y-2 bg-white dark:bg-zinc-900"
          >
            <div className="flex items-center justify-between gap-2">
              <Input
                className="flex-1 h-8 text-sm font-medium"
                value={p.label}
                onChange={(e) => handleUpdate(p.id, { label: e.target.value })}
              />
              <div className="flex items-center gap-2">
                <Switch
                  checked={p.enabled}
                  onCheckedChange={() => handleToggle(p.id)}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleRemove(p.id)}
                >
                  Remove
                </Button>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Select
                value={p.family}
                onValueChange={(v) =>
                  handleUpdate(p.id, { family: v as ProviderFamily })
                }
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(FAMILY_LABELS) as ProviderFamily[]).map((f) => (
                    <SelectItem key={f} value={f}>
                      {FAMILY_LABELS[f]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                className="h-8 text-xs"
                placeholder={`Base URL (default: ${FAMILY_DEFAULT_URL[p.family]})`}
                value={p.baseUrl || ""}
                onChange={(e) =>
                  handleUpdate(p.id, { baseUrl: e.target.value || null })
                }
              />
            </div>
            <Input
              className="h-8 text-xs font-mono"
              placeholder="API key"
              type="password"
              value={p.apiKey}
              onChange={(e) => handleUpdate(p.id, { apiKey: e.target.value })}
            />
          </li>
        ))}
      </ul>

      {isAdding && (
        <div className="border-2 border-dashed rounded p-3 flex flex-col gap-y-2">
          <Input
            placeholder='Label (e.g. "OmniRoute", "OpenRouter", "Groq")'
            value={draft.label || ""}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
          <Select
            value={draft.family || "openai"}
            onValueChange={(v) =>
              setDraft({ ...draft, family: v as ProviderFamily })
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(FAMILY_LABELS) as ProviderFamily[]).map((f) => (
                <SelectItem key={f} value={f}>
                  {FAMILY_LABELS[f]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="Base URL (optional, e.g. http://localhost:20128/v1)"
            value={draft.baseUrl || ""}
            onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
          />
          <Input
            placeholder="API key"
            type="password"
            value={draft.apiKey || ""}
            onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
          />
          <div className="flex gap-2 justify-end">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setIsAdding(false);
                setDraft({ family: "openai", enabled: true });
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleAdd}
              disabled={!draft.label?.trim() || !draft.apiKey?.trim()}
            >
              Add
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default ProviderPanel;
