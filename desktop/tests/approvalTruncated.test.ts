/** Store-level contract: the truncation flag survives into the approval queue.
 * Run with `node --test tests/approvalTruncated.test.ts` */

import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { useApprovalStore } from '../src/state/stores.ts'

beforeEach(() => {
  useApprovalStore.getState().clear()
})

test('pending approval keeps the truncation flag for the panel', () => {
  useApprovalStore.getState().push({
    requestId: 'a1',
    toolName: 'bash',
    args: '{"command": "echo …(+9000 chars)"}',
    argsTruncated: true,
    risk: null,
    target: null,
    reason: null,
  })
  const [approval] = useApprovalStore.getState().queue
  assert.equal(approval.argsTruncated, true)

  useApprovalStore.getState().push({
    requestId: 'a2',
    toolName: 'read_file',
    args: '{"path": "x"}',
    risk: null,
    target: null,
    reason: null,
  })
  const [, plain] = useApprovalStore.getState().queue
  assert.notEqual(plain.argsTruncated, true)
})
