import { createHash, randomBytes } from 'node:crypto'
import { spawn, spawnSync } from 'node:child_process'
import { createReadStream, createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer, request as httpRequest } from 'node:http'
import { homedir, platform, arch } from 'node:os'
import { basename, dirname, extname, isAbsolute, join, delimiter, relative, resolve } from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { createRequire } from 'node:module'
import { x as extractTar } from 'tar'

const require = createRequire(import.meta.url)
const PYTHON_RELEASE = '20260901'
const PYTHON_VERSION = '3.11.16'
const PYTHON_ASSETS = {
  'win32-x64': ['x86_64-pc-windows-msvc', '6be524fa6752af802146a4adc7d098565425b0b1c166e19a5a7a4c8cccb86bf6'],
  'win32-arm64': ['aarch64-pc-windows-msvc', '3a983c8d8e60ac38653bce3063b1875b8deb9816b4e6f58c0623f537530b2025'],
  'linux-x64': ['x86_64-unknown-linux-gnu', 'faa0758583a63f14c5eee516af82738403b59c13edda6fc0a21d953febd89eed'],
  'linux-arm64': ['aarch64-unknown-linux-gnu', 'c1cca4af741e33d47871b298842e6c3272cd9e8f57daf8085359fcbfe2d1a5aa'],
  'darwin-x64': ['x86_64-apple-darwin', '167cc15cf4eeb72944a67bbd2f7120c45fded17d5043d5db64b3144d7adc30ae'],
  'darwin-arm64': ['aarch64-apple-darwin', '50424fa409e8ae84b82a3052522f64695b47dff2158b70bb7358e0ebd6c085c9'],
}

export function parseArguments(argv) {
  const result = {
    command: 'web', noOpen: false, useSystemPython: false, sourceRoot: undefined,
    dataDir: undefined, studioPort: 3000, harnessPort: 3080, runtimePort: 8001,
    mediaPort: 18080, sandboxPort: 8090,
  }
  const values = [...argv]
  if (values[0] && !values[0].startsWith('-')) result.command = values.shift()
  while (values.length) {
    const key = values.shift()
    if (key === '--') continue
    if (key === '--no-open') result.noOpen = true
    else if (key === '--use-system-python') result.useSystemPython = true
    else if (key === '--source-root') result.sourceRoot = resolve(requiredValue(key, values))
    else if (key === '--data-dir') result.dataDir = resolve(requiredValue(key, values))
    else if (key === '--studio-port') result.studioPort = portValue(key, values)
    else if (key === '--harness-port') result.harnessPort = portValue(key, values)
    else if (key === '--runtime-port') result.runtimePort = portValue(key, values)
    else if (key === '--media-port') result.mediaPort = portValue(key, values)
    else if (key === '--sandbox-port') result.sandboxPort = portValue(key, values)
    else throw new Error(`unknown option: ${key}`)
  }
  if (!['web', 'doctor'].includes(result.command)) throw new Error(`unknown command: ${result.command}`)
  return result
}

function requiredValue(key, values) {
  const value = values.shift()
  if (!value) throw new Error(`${key} requires a value`)
  return value
}

function portValue(key, values) {
  const port = Number(requiredValue(key, values))
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`${key} requires a valid port`)
  return port
}

export function defaultDataDirectory(environment = process.env) {
  if (platform() === 'win32') return join(environment.LOCALAPPDATA || homedir(), 'VideoAgentHarness')
  if (platform() === 'darwin') return join(homedir(), 'Library', 'Application Support', 'VideoAgentHarness')
  return join(environment.XDG_DATA_HOME || join(homedir(), '.local', 'share'), 'video-agent-harness')
}

export function loadDotEnv(path, environment = process.env) {
  if (!existsSync(path)) return environment
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/.exec(line)
    if (!match || environment[match[1]] !== undefined) continue
    let value = match[2]
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    environment[match[1]] = value
  }
  return environment
}

export function loadLocalCredentialEnvironment(layout, environment = process.env) {
  loadDotEnv(join(layout.root, '.env'), environment)
  if (!layout.source) return environment

  const workspaceRoot = dirname(layout.root)
  for (const path of [
    join(workspaceRoot, 'cuti-video-agent', '.env'),
    join(workspaceRoot, 'cuti-video-agent', 'services', 'agent', '.env'),
  ]) loadDotEnv(path, environment)
  return environment
}

export function resolveLayout(options, packageRoot) {
  if (options.sourceRoot) {
    const root = options.sourceRoot
    return {
      root,
      videoRuntime: join(root, 'services', 'video-runtime'),
      mediaService: join(root, 'services', 'media-service'),
      sandboxWorker: join(root, 'services', 'sandbox-worker'),
      studio: join(root, 'apps', 'video-studio', 'dist'),
      requirements: join(packageRoot, 'runtime', 'requirements-local.txt'),
      harnessBin: join(root, 'apps', 'cli', 'src', 'bin.ts'),
      bundlePatch: join(root, 'packages', 'bundle', 'video-agent', 'cordis.patch.yml'),
      source: true,
    }
  }
  const runtime = join(packageRoot, 'runtime')
  const dshManifest = require.resolve('@deepseek-ai/dsh/package.json')
  return {
    root: packageRoot,
    videoRuntime: join(runtime, 'video-runtime'),
    mediaService: join(runtime, 'media-service'),
    sandboxWorker: join(runtime, 'sandbox-worker'),
    studio: join(runtime, 'studio'),
    requirements: join(runtime, 'requirements-local.txt'),
    harnessBin: join(dirname(dshManifest), 'lib', 'bin.js'),
    bundlePatch: require.resolve('@cuti-ai/video-agent-bundle/cordis.patch.yml'),
    source: false,
  }
}

async function download(url, destination) {
  const response = await fetch(url, { redirect: 'follow' })
  if (!response.ok || !response.body) throw new Error(`download failed (${response.status}): ${url}`)
  await pipeline(Readable.fromWeb(response.body), createWriteStream(destination))
}

function fileSha256(path) {
  return new Promise((resolveHash, reject) => {
    const hash = createHash('sha256')
    createReadStream(path).on('error', reject).on('data', chunk => hash.update(chunk)).on('end', () => resolveHash(hash.digest('hex')))
  })
}

function pythonExecutable(root) {
  return platform() === 'win32' ? join(root, 'python', 'python.exe') : join(root, 'python', 'bin', 'python3')
}

function commandWorks(command, args, environment = process.env) {
  const result = spawnSync(command, args, { env: environment, stdio: 'ignore', windowsHide: true })
  return result.status === 0
}

function findSystemPython() {
  const candidates = process.env.VIDEO_AGENT_PYTHON
    ? [process.env.VIDEO_AGENT_PYTHON]
    : platform() === 'win32' ? ['py', 'python'] : ['python3', 'python']
  for (const candidate of candidates) {
    const args = basename(candidate).toLowerCase() === 'py' ? ['-3.11', '-c', 'import sys; assert sys.version_info[:2] == (3, 11)'] : ['-c', 'import sys; assert sys.version_info[:2] == (3, 11)']
    if (commandWorks(candidate, args)) return { command: candidate, prefix: basename(candidate).toLowerCase() === 'py' ? ['-3.11'] : [] }
  }
  return undefined
}

export async function ensurePython(dataDirectory, useSystemPython) {
  if (useSystemPython || process.env.VIDEO_AGENT_PYTHON) {
    const system = findSystemPython()
    if (!system) throw new Error('Python 3.11 was requested but could not be found')
    return system
  }
  const installRoot = join(dataDirectory, `python-${PYTHON_VERSION}`)
  const executable = pythonExecutable(installRoot)
  if (existsSync(executable)) return { command: executable, prefix: [] }
  const selected = PYTHON_ASSETS[`${platform()}-${arch()}`]
  if (!selected) throw new Error(`portable Python is unavailable for ${platform()}-${arch()}`)
  mkdirSync(installRoot, { recursive: true })
  const [target, expectedHash] = selected
  const filename = `cpython-${PYTHON_VERSION}+${PYTHON_RELEASE}-${target}-install_only.tar.gz`
  const archive = join(dataDirectory, filename)
  const url = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/${filename.replace('+', '%2B')}`
  console.log(`video-agent-harness: downloading portable Python ${PYTHON_VERSION}`)
  await download(url, archive)
  if (await fileSha256(archive) !== expectedHash) throw new Error('portable Python checksum mismatch')
  await extractTar({ file: archive, cwd: installRoot, strict: true })
  if (!existsSync(executable)) throw new Error('portable Python archive did not contain the expected executable')
  rmSync(archive, { force: true })
  return { command: executable, prefix: [] }
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: 'inherit', windowsHide: true, ...options })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status ?? result.signal}`)
}

export function ensurePythonDependencies(python, layout, dataDirectory) {
  const dependencyRoot = join(dataDirectory, 'python-packages')
  const requirementHash = createHash('sha256').update(readFileSync(layout.requirements)).digest('hex')
  const marker = join(dependencyRoot, '.requirements-sha256')
  const inherited = layout.source && existsSync(join(layout.root, '.runtime-deps', 'windows-py311'))
    ? join(layout.root, '.runtime-deps', 'windows-py311') : ''
  const pythonPath = [dependencyRoot, inherited].filter(Boolean).join(delimiter)
  const probeEnv = { ...process.env, PYTHONPATH: pythonPath }
  if (existsSync(marker) && readFileSync(marker, 'utf8') === requirementHash
    && commandWorks(python.command, [...python.prefix, '-c', 'import fastapi,uvicorn,httpx,docker,numpy'], probeEnv)) {
    return pythonPath
  }
  if (inherited && commandWorks(python.command, [...python.prefix, '-c', 'import fastapi,uvicorn,httpx,docker,numpy'], probeEnv)) return pythonPath
  mkdirSync(dependencyRoot, { recursive: true })
  console.log('video-agent-harness: installing local Python service dependencies')
  run(python.command, [...python.prefix, '-m', 'ensurepip', '--upgrade'], { env: probeEnv })
  run(python.command, [...python.prefix, '-m', 'pip', 'install', '--disable-pip-version-check', '--target', dependencyRoot, '-r', layout.requirements], { env: probeEnv })
  writeFileSync(marker, requirementHash)
  return pythonPath
}

function mediaToolPaths(dataDirectory) {
  const root = join(dataDirectory, 'media-tools')
  const mediaRequire = createRequire(join(root, 'package.json'))
  try {
    return {
      root,
      ffmpeg: mediaRequire('@ffmpeg-installer/ffmpeg').path,
      ffprobe: mediaRequire('@ffprobe-installer/ffprobe').path,
    }
  } catch {
    return { root }
  }
}

export function ensureMediaTools(dataDirectory, environment = process.env) {
  let tools = mediaToolPaths(dataDirectory)
  if (!tools.ffmpeg || !tools.ffprobe || !existsSync(tools.ffmpeg) || !existsSync(tools.ffprobe)) {
    mkdirSync(tools.root, { recursive: true })
    const manifest = join(tools.root, 'package.json')
    if (!existsSync(manifest)) writeFileSync(manifest, '{"private":true}\n')
    console.log('video-agent-harness: installing separately licensed local FFmpeg and FFprobe tools')
    const npm = npmInvocation(environment)
    run(npm.command, [...npm.prefix,
      'install', '--prefix', tools.root, '--no-save', '--no-audit', '--no-fund',
      '@ffmpeg-installer/ffmpeg@1.1.0', '@ffprobe-installer/ffprobe@2.1.2',
    ], { env: environment })
    tools = mediaToolPaths(dataDirectory)
  }
  if (!tools.ffmpeg || !tools.ffprobe) throw new Error('local FFmpeg installation did not provide both executables')
  return [...new Set([dirname(tools.ffmpeg), dirname(tools.ffprobe), environment.PATH || ''])].filter(Boolean).join(delimiter)
}

function npmInvocation(environment) {
  if (platform() !== 'win32') return { command: 'npm', prefix: [] }
  const candidates = [
    environment.npm_execpath?.endsWith('npm-cli.js') ? environment.npm_execpath : undefined,
    join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js'),
  ].filter(Boolean)
  const located = spawnSync('where.exe', ['npm.cmd'], { encoding: 'utf8', windowsHide: true })
  if (located.status === 0) {
    for (const command of located.stdout.split(/\r?\n/).filter(Boolean)) {
      candidates.push(join(dirname(command.trim()), 'node_modules', 'npm', 'bin', 'npm-cli.js'))
    }
  }
  const cli = candidates.find(existsSync)
  if (!cli) throw new Error('npm CLI could not be located for local FFmpeg installation')
  return { command: process.execPath, prefix: [cli] }
}

function prefixLines(name, stream) {
  let pending = ''
  stream?.setEncoding('utf8')
  stream?.on('data', chunk => {
    pending += chunk
    const lines = pending.split(/\r?\n/)
    pending = lines.pop() || ''
    for (const line of lines) if (line) console.log(`[${name}] ${line}`)
  })
}

function spawnManaged(name, command, args, options) {
  const child = spawn(command, args, { ...options, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
  prefixLines(name, child.stdout)
  prefixLines(name, child.stderr)
  child.on('error', error => console.error(`[${name}] ${error.message}`))
  return { name, child }
}

async function waitFor(url, name, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
      lastError = new Error(`${response.status} ${response.statusText}`)
    } catch (error) { lastError = error }
    await new Promise(resolveWait => setTimeout(resolveWait, 250))
  }
  throw new Error(`${name} did not become ready: ${lastError?.message || 'timeout'}`)
}

function contentType(path) {
  return ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.mp3': 'audio/mpeg', '.mp4': 'video/mp4' })[extname(path).toLowerCase()] || 'application/octet-stream'
}

export function createStudioGateway(studioRoot, runtimePort, studioPort) {
  if (!existsSync(join(studioRoot, 'index.html'))) throw new Error(`Video Studio build is missing: ${studioRoot}`)
  const proxyPrefixes = ['/api/', '/chat-v1/', '/files/']
  const server = createServer((incoming, response) => {
    const requestPath = new URL(incoming.url || '/', 'http://127.0.0.1').pathname
    if (proxyPrefixes.some(prefix => requestPath.startsWith(prefix))) {
      const proxied = httpRequest({ hostname: '127.0.0.1', port: runtimePort, path: incoming.url, method: incoming.method, headers: incoming.headers }, upstream => {
        response.writeHead(upstream.statusCode || 502, upstream.headers)
        upstream.pipe(response)
      })
      proxied.on('error', error => { response.writeHead(502); response.end(error.message) })
      incoming.pipe(proxied)
      return
    }
    const requestRelative = decodeURIComponent(requestPath).replace(/^\/+/, '')
    let path = resolve(studioRoot, requestRelative || 'index.html')
    const escaped = relative(resolve(studioRoot), path)
    if (escaped.startsWith('..') || isAbsolute(escaped) || !existsSync(path) || extname(path) === '') path = join(studioRoot, 'index.html')
    response.setHeader('Content-Type', contentType(path))
    createReadStream(path).on('error', () => { response.writeHead(404); response.end('Not found') }).pipe(response)
  })
  return new Promise((resolveServer, reject) => {
    server.once('error', reject)
    server.listen(studioPort, '127.0.0.1', () => resolveServer(server))
  })
}

function openBrowser(url) {
  const command = platform() === 'win32' ? 'cmd' : platform() === 'darwin' ? 'open' : 'xdg-open'
  const args = platform() === 'win32' ? ['/c', 'start', '', url] : [url]
  spawn(command, args, { detached: true, stdio: 'ignore', windowsHide: true }).unref()
}

function importWindowsProxy(environment) {
  if (platform() !== 'win32' || environment.HTTP_PROXY || environment.HTTPS_PROXY) return
  const query = spawnSync('reg', ['query', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'], { encoding: 'utf8', windowsHide: true })
  if (query.status !== 0 || !/ProxyEnable\s+REG_DWORD\s+0x1/i.test(query.stdout)) return
  const match = /ProxyServer\s+REG_SZ\s+([^\r\n]+)/i.exec(query.stdout)
  if (!match) return
  const raw = match[1].trim()
  const values = Object.fromEntries(raw.split(';').map(item => item.split('=', 2)).filter(item => item.length === 2))
  const normalize = value => value && (/^[a-z]+:\/\//i.test(value) ? value : `http://${value}`)
  environment.HTTP_PROXY = normalize(values.http || raw)
  environment.HTTPS_PROXY = normalize(values.https || values.http || raw)
  environment.NODE_USE_ENV_PROXY = '1'
  environment.NO_PROXY ||= '127.0.0.1,localhost'
}

export async function runLocalWeb(options, packageRoot) {
  const layout = resolveLayout(options, packageRoot)
  const dataDirectory = options.dataDir || defaultDataDirectory()
  mkdirSync(dataDirectory, { recursive: true })
  loadLocalCredentialEnvironment(layout)
  importWindowsProxy(process.env)
  const python = await ensurePython(dataDirectory, options.useSystemPython)
  const dependencyPath = ensurePythonDependencies(python, layout, dataDirectory)
  const mediaPath = ensureMediaTools(dataDirectory)
  const secret = randomBytes(32).toString('hex')
  const mediaDirectory = join(dataDirectory, 'media')
  mkdirSync(mediaDirectory, { recursive: true })
  const common = {
    ...process.env,
    PATH: mediaPath,
    PYTHONUNBUFFERED: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    STORAGE_BACKEND: 'local',
    LOCAL_STORAGE_DIR: mediaDirectory,
    PUBLIC_BASE_URL: `http://127.0.0.1:${options.runtimePort}`,
  }
  const pythonArgs = (module, port) => [...python.prefix, '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', String(port), '--no-server-header']
  const processes = []
  let gateway
  let stopping = false
  const stop = () => {
    if (stopping) return
    stopping = true
    gateway?.close()
    for (const { child } of processes.reverse()) if (!child.killed) child.kill('SIGTERM')
  }
  process.once('SIGINT', stop)
  process.once('SIGTERM', stop)
  const service = (name, root, module, port, extra) => {
    const environment = { ...common, ...extra, PYTHONPATH: [dependencyPath, root].filter(Boolean).join(delimiter) }
    const managed = spawnManaged(name, python.command, pythonArgs(module, port), { cwd: root, env: environment })
    processes.push(managed)
    return managed
  }
  service('media', layout.mediaService, 'app.main:app', options.mediaPort, {
    ENVIRONMENT: 'development', WATERMARK_IMAGE_PATH: '',
  })
  service('sandbox', layout.sandboxWorker, 'app.main:app', options.sandboxPort, {
    SANDBOX_ENVIRONMENT: 'development', SANDBOX_UNSAFE_DEV_MODE: 'true',
    SANDBOX_INTERNAL_TOKEN: 'video-harness-sandbox-local',
    SANDBOX_STAGING_ROOT: join(dataDirectory, 'sandbox'),
  })
  try {
    await Promise.all([
      waitFor(`http://127.0.0.1:${options.mediaPort}/healthz`, 'Media Service'),
      waitFor(`http://127.0.0.1:${options.sandboxPort}/healthz`, 'Sandbox Worker'),
    ])
  } catch (error) {
    stop()
    throw error
  }
  const harnessEnvironment = {
    ...common,
    DSH_HOME: join(dataDirectory, 'deepseek-harness'),
    VIDEO_RUNTIME_URL: `http://127.0.0.1:${options.runtimePort}`,
    VIDEO_RUNTIME_SERVICE_TOKEN: 'video-harness-runtime-local',
    VIDEO_RUNTIME_LOCAL_USER_ID: 'local-user',
  }
  const harnessArgs = layout.source
    ? ['--import', 'tsx/esm', layout.harnessBin, 'web', '--patch', layout.bundlePatch, '--no-open', '--port', String(options.harnessPort)]
    : [layout.harnessBin, 'web', '--patch', layout.bundlePatch, '--no-open', '--port', String(options.harnessPort)]
  processes.push(spawnManaged('harness', process.execPath, harnessArgs, { cwd: layout.root, env: harnessEnvironment }))
  service('runtime', layout.videoRuntime, 'app.video_runtime.standalone:app', options.runtimePort, {
    ACCOUNT_BACKEND: 'env', VIDEO_AGENT_BACKEND: 'deepseek',
    VIDEO_RUNTIME_LOCAL_STATE_PATH: join(dataDirectory, 'video-runtime-state.json'),
    VIDEO_RUNTIME_SERVICE_TOKEN: 'video-harness-runtime-local', VIDEO_RUNTIME_LOCAL_USER_ID: 'local-user',
    VIDEO_PLUGIN_PATHS: join(layout.videoRuntime, 'plugins'), VIDEO_CAPABILITY_GRANT_SECRET: secret,
    VIDEO_INCREMENTAL_ENGINE_ENABLED: 'true', VIDEO_STAGED_PLANNING_ENABLED: 'true',
    VIDEO_CONTINUOUS_PLAN_PATCH_ENABLED: 'true', DEEPSEEK_HARNESS_URL: `http://127.0.0.1:${options.harnessPort}`,
    MEDIA_SERVICE_URL: `http://127.0.0.1:${options.mediaPort}`,
    DEEP_AGENT_V2_SANDBOX_ENABLED: 'true', DEEP_AGENT_V2_SANDBOX_WORKER_URL: `http://127.0.0.1:${options.sandboxPort}`,
    DEEP_AGENT_V2_SANDBOX_TOKEN: 'video-harness-sandbox-local', VIDEO_SANDBOX_WORKER_URL: `http://127.0.0.1:${options.sandboxPort}`,
    VIDEO_SANDBOX_WORKER_TOKEN: 'video-harness-sandbox-local',
  })
  try {
    await Promise.all([
      waitFor(`http://127.0.0.1:${options.harnessPort}`, 'DeepSeek Harness'),
      waitFor(`http://127.0.0.1:${options.runtimePort}/health`, 'Video Runtime'),
    ])
    gateway = await createStudioGateway(layout.studio, options.runtimePort, options.studioPort)
  } catch (error) {
    stop()
    throw error
  }
  const url = `http://127.0.0.1:${options.studioPort}/#/zh/create`
  console.log(`\nVideo Agent Harness is ready: ${url}`)
  console.log(`Local data: ${dataDirectory}\n`)
  if (!options.noOpen) openBrowser(url)

  await new Promise((resolveDone, reject) => {
    for (const { name, child } of processes) child.once('exit', (code, signal) => {
      if (stopping) resolveDone()
      else reject(new Error(`${name} exited unexpectedly (${code ?? signal})`))
    })
  }).finally(stop)
}

export async function doctor(options, packageRoot) {
  const layout = resolveLayout(options, packageRoot)
  const dataDirectory = options.dataDir || defaultDataDirectory()
  const system = findSystemPython()
  const rows = [
    ['Node.js', process.version, true],
    ['Portable Python', pythonExecutable(join(dataDirectory, `python-${PYTHON_VERSION}`)), existsSync(pythonExecutable(join(dataDirectory, `python-${PYTHON_VERSION}`)))],
    ['System Python 3.11', system?.command || 'not found', Boolean(system)],
    ['Local FFmpeg/FFprobe', join(dataDirectory, 'media-tools'), Boolean(mediaToolPaths(dataDirectory).ffmpeg && mediaToolPaths(dataDirectory).ffprobe)],
    ['Video Runtime', layout.videoRuntime, existsSync(layout.videoRuntime)],
    ['Media Service', layout.mediaService, existsSync(layout.mediaService)],
    ['Sandbox Worker', layout.sandboxWorker, existsSync(layout.sandboxWorker)],
    ['Video Studio', layout.studio, existsSync(join(layout.studio, 'index.html'))],
  ]
  for (const [name, value, ok] of rows) console.log(`${ok ? 'OK' : '--'}  ${name}: ${value}`)
  const servicesReady = rows.slice(3).every(row => row[2])
  const pythonReady = options.useSystemPython ? Boolean(system) : true
  return servicesReady && pythonReady
}
