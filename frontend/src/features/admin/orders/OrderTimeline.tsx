import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Collapse from '@mui/material/Collapse';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { OrderEvent } from '@/api/types';
import { formatDateTime } from '@/lib/format';

function summary(e: OrderEvent): string {
  const p = e.payload ?? {};
  const parts: string[] = [];
  if (typeof p.message === 'string') parts.push(p.message);
  if (typeof p.sd_id === 'string') parts.push(`SD ${p.sd_id}`);
  if ('from' in p && 'to' in p) parts.push(`${String(p.from)} → ${String(p.to)}`);
  if (typeof p.what === 'string') parts.push(p.what);
  if (Array.isArray(p.errors)) parts.push(`${p.errors.length} ✕`);
  return parts.join(' · ');
}

function Item({ e }: { e: OrderEvent }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const actor = `${t(`aorders.actor_${e.actor_type}`, { defaultValue: e.actor_type })}${e.actor_id ? ` #${e.actor_id}` : ''}`;
  return (
    <Stack direction="row" spacing={1.5} component="li" sx={{ listStyle: 'none', pb: 1.5 }}>
      <Box
        aria-hidden
        sx={{
          width: 10,
          height: 10,
          mt: 0.7,
          borderRadius: '50%',
          bgcolor:
            e.event.includes('error') || e.event === 'outcome_unknown'
              ? 'error.main'
              : 'primary.main',
          flexShrink: 0,
        }}
      />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" fontWeight={600}>
          {t(`orderEvent.${e.event}`, { defaultValue: e.event })}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {formatDateTime(e.at)} · {actor}
        </Typography>
        {summary(e) && (
          <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>
            {summary(e)}
          </Typography>
        )}
        {e.payload && Object.keys(e.payload).length > 0 && (
          <>
            <Button size="small" onClick={() => setOpen((v) => !v)} sx={{ display: 'block', p: 0 }}>
              {open ? t('aorders.hideDetails') : t('aorders.showDetails')}
            </Button>
            <Collapse in={open} unmountOnExit>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1,
                  bgcolor: 'action.hover',
                  borderRadius: 1,
                  overflow: 'auto',
                  fontSize: 12,
                  maxHeight: 240,
                }}
              >
                {JSON.stringify(e.payload, null, 2)}
              </Box>
            </Collapse>
          </>
        )}
      </Box>
    </Stack>
  );
}

export function OrderTimeline({ events }: { events: OrderEvent[] }) {
  const { t } = useTranslation();
  if (events.length === 0)
    return <Typography color="text.secondary">{t('aorders.noEvents')}</Typography>;
  return (
    <Box component="ol" sx={{ m: 0, p: 0 }} aria-label={t('aorders.timeline')}>
      {events.map((e, i) => (
        <Item key={`${e.at}-${i}`} e={e} />
      ))}
    </Box>
  );
}
