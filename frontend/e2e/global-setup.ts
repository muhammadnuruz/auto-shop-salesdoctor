import { request } from '@playwright/test';
import { FAKE_SD } from './helpers';

/** Start every run from a clean fake SD (no scripted failures left over from a previous run). */
export default async function globalSetup(): Promise<void> {
  const ctx = await request.newContext();
  const res = await ctx.post(`${FAKE_SD}/__reset`);
  if (!res.ok()) throw new Error(`fake SD not reachable at ${FAKE_SD}: HTTP ${res.status()}`);
  await ctx.dispose();
}
