// Curated copy targets (DECISIONS: root.near, seriousfarmer.near only).
import { TARGET_NOTES } from './target-notes'

export type CuratedTarget = { id: string; note: string }

export const CURATED: CuratedTarget[] = ['root.near', 'seriousfarmer.near'].map((id) => ({ id, note: TARGET_NOTES[id] }))
