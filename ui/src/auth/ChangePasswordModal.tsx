import { useTranslation } from 'react-i18next'
import { Modal, useToast } from '../components/ui'
import { ChangePasswordForm } from './ChangePasswordForm'

/** Voluntary password change, launched from the Topbar (dismissable, unlike the forced screen). */
export function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  return (
    <Modal open={open} onClose={onClose} title={t('auth.password.changeTitle')}>
      <ChangePasswordForm
        submitLabel={t('auth.password.update')}
        onSuccess={() => {
          toast.success(t('auth.password.updated'))
          onClose()
        }}
      />
    </Modal>
  )
}
