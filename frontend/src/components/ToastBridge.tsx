import { useSnackbar } from 'notistack';
import { useEffect } from 'react';
import { setToastEnqueue } from '@/lib/toast';

export function ToastBridge() {
  const { enqueueSnackbar } = useSnackbar();
  useEffect(() => {
    setToastEnqueue(enqueueSnackbar);
    return () => setToastEnqueue(null);
  }, [enqueueSnackbar]);
  return null;
}
