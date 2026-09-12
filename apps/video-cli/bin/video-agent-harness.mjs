#!/usr/bin/env node
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { doctor, parseArguments, runLocalWeb, setupLocalEnvironment } from '../src/local-runtime.mjs'

const packageRoot = resolve(fileURLToPath(new URL('..', import.meta.url)))

try {
  const options = parseArguments(process.argv.slice(2))
  if (options.command === 'doctor') {
    if (!await doctor(options, packageRoot)) process.exitCode = 1
  } else if (options.command === 'setup') {
    await setupLocalEnvironment(options, packageRoot)
  } else {
    await runLocalWeb(options, packageRoot)
  }
} catch (error) {
  console.error(`video-agent-harness: ${error instanceof Error ? error.message : String(error)}`)
  process.exitCode = 1
}
