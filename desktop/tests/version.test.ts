import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

function read(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), 'utf8')
}

function projectVersion(): string {
  const pyproject = read('../../pyproject.toml')
  const match = /^version = "([^"]+)"/m.exec(pyproject.slice(pyproject.indexOf('[project]')))
  assert.ok(match, 'pyproject.toml has no [project] version')
  return match[1]
}

function jsonVersion(relative: string): string {
  return JSON.parse(read(relative)).version as string
}

function cargoTomlVersion(): string {
  const manifest = read('../src-tauri/Cargo.toml')
  const match = /^version = "([^"]+)"/m.exec(manifest.slice(manifest.indexOf('[package]')))
  assert.ok(match, 'Cargo.toml has no [package] version')
  return match[1]
}

function cargoLockVersion(): string {
  const blocks = read('../src-tauri/Cargo.lock').split('\n[[package]]\n')
  const crate = blocks.find((block) => /^name = "zenagent-desktop"$/m.test(block))
  assert.ok(crate, 'Cargo.lock has no zenagent-desktop package')
  const match = /^version = "([^"]+)"/m.exec(crate)
  assert.ok(match, 'zenagent-desktop has no version in Cargo.lock')
  return match[1]
}

test('desktop manifests share the project version', () => {
  const version = projectVersion()
  assert.equal(jsonVersion('../package.json'), version)
  assert.equal(jsonVersion('../src-tauri/tauri.conf.json'), version)
  assert.equal(cargoTomlVersion(), version)
  assert.equal(cargoLockVersion(), version)
})
