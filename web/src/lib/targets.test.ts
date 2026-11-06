import { describe, expect, it } from 'vitest'
import { CURATED } from './targets'

const ALLOWED = new Set(['root.near', 'seriousfarmer.near'])

describe('curated target notes', () => {
  it('lists only root.near and seriousfarmer.near', () => {
    expect(CURATED.map((c) => c.id)).toEqual(['root.near', 'seriousfarmer.near'])
  })

  for (const c of CURATED) {
    describe(c.id, () => {
      it('has a real note of 2-4 short sentences', () => {
        expect(c.note).not.toMatch(/pending/i)
        const sentences = c.note.split(/(?<=\.)\s+/).filter(Boolean)
        expect(sentences.length).toBeGreaterThanOrEqual(2)
        expect(sentences.length).toBeLessThanOrEqual(4)
        for (const s of sentences) expect(s.length).toBeLessThanOrEqual(120)
      })

      it('carries the small-sample and market-regime warnings', () => {
        expect(c.note).toMatch(/small sample/i)
        expect(c.note).toMatch(/meme season/i)
      })

      it('names no other wallet', () => {
        const accounts = c.note.match(/\b[a-z0-9_-]+(\.[a-z0-9_-]+)*\.near\b/g) ?? []
        for (const a of accounts) expect(ALLOWED.has(a)).toBe(true)
      })

      it('has no hype words', () => {
        expect(c.note).not.toMatch(/!|moon|guarantee|huge|massive|100x/i)
      })
    })
  }
})
