import BlockIcon from '@mui/icons-material/Block';
import InboxOutlinedIcon from '@mui/icons-material/InboxOutlined';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError, isStatus } from '@/lib/errors';

export function EmptyState({
  title,
  text,
  action,
}: {
  title: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <Stack alignItems="center" spacing={1} sx={{ py: 6, px: 2, textAlign: 'center' }}>
      <InboxOutlinedIcon color="disabled" sx={{ fontSize: 48 }} />
      <Typography variant="h3" component="p">
        {title}
      </Typography>
      {text && <Typography color="text.secondary">{text}</Typography>}
      {action}
    </Stack>
  );
}

/** 403 -> "no permission" UI, anything else -> message + retry. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  if (isStatus(error, 403)) return <ForbiddenState />;
  return (
    <Alert
      severity="error"
      role="alert"
      sx={{ my: 2 }}
      action={
        onRetry && (
          <Button color="inherit" size="small" onClick={onRetry}>
            {t('common.retry')}
          </Button>
        )
      }
    >
      <AlertTitle>{t('errors.loadFailed')}</AlertTitle>
      {describeError(error, t)}
    </Alert>
  );
}

export function ForbiddenState() {
  const { t } = useTranslation();
  return (
    <Stack alignItems="center" spacing={1} sx={{ py: 8, textAlign: 'center' }} role="alert">
      <BlockIcon color="warning" sx={{ fontSize: 56 }} />
      <Typography variant="h2">{t('errors.forbiddenTitle')}</Typography>
      <Typography color="text.secondary">{t('errors.forbidden')}</Typography>
    </Stack>
  );
}

export function TableSkeleton({ rows = 6, cols }: { rows?: number; cols: number }) {
  return (
    <TableBody aria-busy="true">
      {Array.from({ length: rows }, (_, r) => (
        <TableRow key={r}>
          {Array.from({ length: cols }, (_, c) => (
            <TableCell key={c}>
              <Skeleton variant="text" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </TableBody>
  );
}

export function CardSkeleton({ lines = 4 }: { lines?: number }) {
  return (
    <Box aria-busy="true">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} variant="text" height={32} />
      ))}
    </Box>
  );
}
