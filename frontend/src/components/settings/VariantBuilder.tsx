import { ProviderConfig, VariantModelConfigInput } from "../../types";
import { Input } from "../ui/input";
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
  variantConfigs: VariantModelConfigInput[] | null;
  onChange: (configs: VariantModelConfigInput[] | null) => void;
}

function VariantBuilder({ providers, variantConfigs, onChange }: Props) {
  const enabledProviders = providers.filter((p) => p.enabled);
  const configs = variantConfigs || [];

  const handleAddVariant = () => {
    if (enabledProviders.length === 0) return;
    const first = enabledProviders[0];
    const next: VariantModelConfigInput = {
      family: first.family,
      model_id: "",
      label: `Variant ${configs.length + 1}`,
      api_key: first.apiKey,
      base_url: first.baseUrl,
      reasoning_effort: null,
    };
    onChange([...configs, next]);
  };

  const handleRemoveVariant = (idx: number) => {
    const next = configs.filter((_, i) => i !== idx);
    onChange(next.length === 0 ? null : next);
  };

  const handleUpdateVariant = (
    idx: number,
    patch: Partial<VariantModelConfigInput>
  ) => {
    onChange(configs.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const handleProviderChange = (idx: number, providerId: string) => {
    const provider = providers.find((p) => p.id === providerId);
    if (!provider) return;
    handleUpdateVariant(idx, {
      family: provider.family,
      api_key: provider.apiKey,
      base_url: provider.baseUrl,
    });
  };

  const findProviderForVariant = (cfg: VariantModelConfigInput) => {
    return enabledProviders.find(
      (p) =>
        p.family === cfg.family &&
        p.apiKey === cfg.api_key &&
        p.baseUrl === cfg.base_url
    );
  };

  return (
    <div className="flex flex-col gap-y-3 border rounded-md p-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-sm">Variants</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Build the per-variant model list. When set, overrides the automatic
            key-based selection. Leave empty to use the default behavior.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={handleAddVariant}
          disabled={enabledProviders.length === 0}
        >
          + Add variant
        </Button>
      </div>

      {enabledProviders.length === 0 && (
        <p className="text-xs text-gray-500 italic">
          Add and enable at least one provider above to configure variants.
        </p>
      )}

      {configs.length === 0 && enabledProviders.length > 0 && (
        <p className="text-xs text-gray-500 italic">
          No variants configured. The backend will pick models based on
          available API keys (legacy behavior).
        </p>
      )}

      <ul className="flex flex-col gap-y-2">
        {configs.map((cfg, idx) => {
          const currentProvider = findProviderForVariant(cfg);
          return (
            <li
              key={idx}
              className="border rounded p-3 flex flex-col gap-y-2 bg-white dark:bg-zinc-900"
            >
              <div className="flex items-center justify-between gap-2">
                <Input
                  className="flex-1 h-8 text-sm font-medium"
                  value={cfg.label}
                  onChange={(e) =>
                    handleUpdateVariant(idx, { label: e.target.value })
                  }
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleRemoveVariant(idx)}
                >
                  Remove
                </Button>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Select
                  value={currentProvider?.id || ""}
                  onValueChange={(v) => handleProviderChange(idx, v)}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Pick provider" />
                  </SelectTrigger>
                  <SelectContent>
                    {enabledProviders.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  className="h-8 text-xs font-mono"
                  placeholder="Model id (e.g. gpt-5.5, antigravity/gemini-3.6-flash-high)"
                  value={cfg.model_id}
                  onChange={(e) =>
                    handleUpdateVariant(idx, { model_id: e.target.value })
                  }
                />
              </div>
              <Input
                className="h-8 text-xs"
                placeholder="Reasoning effort (optional: none/low/medium/high/xhigh)"
                value={cfg.reasoning_effort || ""}
                onChange={(e) =>
                  handleUpdateVariant(idx, {
                    reasoning_effort: e.target.value || null,
                  })
                }
              />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default VariantBuilder;
