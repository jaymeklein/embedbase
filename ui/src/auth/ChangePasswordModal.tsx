import { Modal, useToast } from '../components/ui'
import { ChangePasswordForm } from './ChangePasswordForm'

/** Voluntary password change, launched from the Topbar (dismissable, unlike the forced screen). */
export function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  return (
    <Modal open={open} onClose={onClose} title="Change password">
      <ChangePasswordForm
        submitLabel="Update password"
        onSuccess={() => {
          toast.success('Password updated.')
          onClose()
        }}
      />
    </Modal>
  )
}
