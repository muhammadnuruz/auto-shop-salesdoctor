import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { linksApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { LinkStatsDay } from '@/api/types';
import { ErrorState } from '@/components/States';
import { formatAmount } from '@/lib/format';

const PERIODS = [7, 30, 90] as const;

/** Tiny dependency-free bar chart of orders per day (logins as a lighter bar behind). */
function DailyBars({ days }: { days: LinkStatsDay[] }) {
  const { t } = useTranslation();
  const max = Math.max(1, ...days.map((d) => Math.max(d.orders, d.logins)));
  return (
    <Box
      role="img"
      aria-label={t('linkStats.daily')}
      sx={{ display: 'flex', alignItems: 'flex-end', gap: '2px', height: 80, mb: 1 }}
    >
      {days.map((d) => (
        <Box
          key={d.date}
          title={`${dayjs(d.date).format('DD.MM')}: ${t('linkStats.colOrders')} ${d.orders}, ${t('linkStats.colLogins')} ${d.logins}`}
          sx={{ flex: 1, height: '100%', position: 'relative', minWidth: 2 }}
        >
          <Box
            sx={{
              position: 'absolute',
              bottom: 0,
              width: '100%',
              height: `${(d.logins / max) * 100}%`,
              bgcolor: 'action.selected',
              borderRadius: '2px 2px 0 0',
            }}
          />
          <Box
            sx={{
              position: 'absolute',
              bottom: 0,
              left: '20%',
              width: '60%',
              height: `${(d.orders / max) * 100}%`,
              bgcolor: 'primary.main',
              borderRadius: '2px 2px 0 0',
            }}
          />
        </Box>
      ))}
    </Box>
  );
}

export function LinkStatsPanel({ linkId }: { linkId: number }) {
  const { t } = useTranslation();
  const [days, setDays] = useState<number>(30);
  const stats = useQuery({
    queryKey: qk.admin.links.stats(linkId, days),
    queryFn: () => linksApi.stats(linkId, days),
    placeholderData: keepPreviousData,
  });

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
        flexWrap="wrap"
        useFlexGap
        spacing={1}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          {t('linkStats.title')}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={days}
          onChange={(_, v: number | null) => v && setDays(v)}
          aria-label={t('linkStats.period')}
        >
          {PERIODS.map((p) => (
            <ToggleButton key={p} value={p}>
              {t('linkStats.days', { n: p })}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>
      {stats.isPending ? (
        <Skeleton variant="rounded" height={160} />
      ) : stats.isError ? (
        <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
      ) : (
        <>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(5, 1fr)' },
              gap: 1,
              mb: 2,
            }}
          >
            {[
              [t('linkStats.periodOrders'), String(stats.data.orders.period.orders)],
              [t('linkStats.periodSum'), formatAmount(stats.data.orders.period.sum)],
              [t('linkStats.periodLogins'), String(stats.data.orders.period.logins)],
              [t('linkStats.uniqueClients'), String(stats.data.unique_clients)],
              [t('linkStats.allTimeOrders'), String(stats.data.orders.all_time.orders)],
            ].map(([label, value]) => (
              <Paper key={label} variant="outlined" sx={{ p: 1, textAlign: 'center' }}>
                <Typography variant="h3" component="div">
                  {value}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {label}
                </Typography>
              </Paper>
            ))}
          </Box>
          <DailyBars days={stats.data.daily} />
          {stats.data.top_clients.length === 0 ? (
            <Typography color="text.secondary">{t('linkStats.noData')}</Typography>
          ) : (
            <>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>
                {t('linkStats.topClients')}
              </Typography>
              <Table size="small" aria-label={t('linkStats.topClients')}>
                <TableHead>
                  <TableRow>
                    <TableCell>{t('reports.colName')}</TableCell>
                    <TableCell align="right">{t('linkStats.colOrders')}</TableCell>
                    <TableCell align="right">{t('linkStats.colSum')}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {stats.data.top_clients.map((c) => (
                    <TableRow key={c.client_id}>
                      <TableCell>{c.name ?? `#${c.client_id}`}</TableCell>
                      <TableCell align="right">{c.orders}</TableCell>
                      <TableCell align="right">{formatAmount(c.sum)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
        </>
      )}
    </Paper>
  );
}
