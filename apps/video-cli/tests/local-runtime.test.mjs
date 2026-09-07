import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { createStudioGateway, loadDotEnv, parseArguments } from '../src/local-runtime.mjs'

test('parses portable local web options', () => {
  assert.deepEqual(parseArguments([
    'web', '--no-open', '--use-system-python', '--runtime-port', '8101',
  ]), {
    command: 'web', noOpen: true, useSystemPython: true, sourceRoot: undefined,
    dataDir: undefined, studioPort: 3000, harnessPort: 3080, runtimePort: 8101,
    mediaPort: 18080, sandboxPort: 8090,
  })
})

test('rejects invalid ports', () => {
  assert.throws(() => parseArguments(['web', '--studio-port', '0']), /valid port/)
})

test('loads dotenv without overriding the caller environment', () => {
  const directory = mkdtempSync(join(tmpdir(), 'video-agent-cli-'))
  const path = join(directory, '.env')
  writeFileSync(path, 'OPENAI_API_KEY="from-file"\nWAVESPEED_API_KEY=media-key\n')
  const environment = { OPENAI_API_KEY: 'from-shell' }
  loadDotEnv(path, environment)
  assert.equal(environment.OPENAI_API_KEY, 'from-shell')
  assert.equal(environment.WAVESPEED_API_KEY, 'media-key')
})

test('studio gateway serves the single-page application', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'video-agent-studio-'))
  writeFileSync(join(directory, 'index.html'), '<main>video-studio</main>')
  const gateway = await createStudioGateway(directory, 65534, 0)
  try {
    const { port } = gateway.address()
    const response = await fetch(`http://127.0.0.1:${port}/create/thread-id`)
    assert.equal(response.status, 200)
    assert.equal(await response.text(), '<main>video-studio</main>')
  } finally {
    gateway.close()
  }
})
