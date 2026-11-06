/** One quiet line above the transcript when a release is waiting, mirroring the
 * TUI's update notice — but this one can install: the desktop app and its
 * bundled runtime travel together, so the only correct upgrade is the bundle. */

import { useT } from '../i18n/locale'
import { useAppStore } from '../state/appStore'
import { useApprovalStore, useSettingsStore } from '../state/stores'
import { bannerFor } from '../state/update'
import { useUpdateStore } from '../state/updateStore'
import { XIcon } from './icons'
import css from './NoticeBanner.module.css'
import own from './UpdateBanner.module.css'

export function UpdateBanner() {
  const t = useT()
  const cache = useUpdateStore((state) => state.cache)
  const phase = useUpdateStore((state) => state.phase)
  const install = useUpdateStore((state) => state.install)
  const dismiss = useUpdateStore((state) => state.dismiss)
  const busy = useAppStore((state) => state.busy)
  const pendingApprovals = useApprovalStore((state) => state.queue.length)
  const updateChecks = useSettingsStore((state) => state.updateChecks)

  const banner = bannerFor({
    cache,
    current: __APP_VERSION__,
    enabled: __UPDATE_ENABLED__ && updateChecks,
    busy,
    pendingApprovals,
    phase,
  })
  if (banner.kind === 'hidden') return null

  const version = 'version' in banner ? banner.version : ''
  const text =
    banner.kind === 'available'
      ? t('update.available', { version })
      : banner.kind === 'blocked'
        ? t('update.blocked', { version })
        : banner.kind === 'installing'
          ? t('update.installing', { version })
          : t('update.failed')

  return (
    <div className={css.root} data-kind={banner.kind === 'failed' ? 'error' : 'info'} role="status">
      <span className={css.text}>{text}</span>
      {banner.kind === 'available' && (
        <button type="button" className={own.action} onClick={() => void install()}>
          {t('update.install')}
        </button>
      )}
      <button
        type="button"
        className={css.dismiss}
        aria-label={t('notice.dismiss')}
        onClick={dismiss}
      >
        <XIcon size={12} />
      </button>
    </div>
  )
}
