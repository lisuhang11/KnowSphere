import type { ModelInfo } from '@/api/models'
import { modelDisplayName } from '@/utils/modelDefaults'

export function buildModelLabelMap(models: ModelInfo[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const m of models) {
    map.set(m.id, modelDisplayName(m))
    map.set(m.name, modelDisplayName(m))
  }
  return map
}

export function labelForModelId(id: string, labels: Map<string, string>): string {
  return labels.get(id) ?? id
}
