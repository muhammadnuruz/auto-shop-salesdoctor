import DownloadIcon from '@mui/icons-material/Download';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import LinearProgress from '@mui/material/LinearProgress';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableFooter from '@mui/material/TableFooter';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { reportsApi, type ReportParams } from '@/api/admin';
import { qk } from '@/api/keys';
import type { ReportGroupBy } from '@/api/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { saveBlob } from '@/lib/download';
import { formatAmount } from '@/lib/format';
import { dealerNow } from '@/lib/tashkent';
import { useCan } from '../AdminContext';

const GROUPS: ReportGroupBy[] = ['client', 'product', 'category', 'link', 'agent', 'day'];
const FMT = 'YYYY-MM-DD';

type Preset = 'today' | '7' | '30' | 'month';

function presetRange(p: Preset): { from: string; to: string } {
  const today = dealerNow(); // report days are Tashkent days, whatever the browser zone
  switch (p) {
    case 'today':
      return { from: today.format(FMT), to: today.format(FMT) };
    case '7':
      return { from: today.subtract(6, 'day').format(FMT), to: today.format(FMT) };
    case 'month':
      return { from: today.startOf('month').format(FMT), to: today.format(FMT) };
    default:
      return { from: today.subtract(29, 'day').format(FMT), to: today.format(FMT) };
  }
}

export default function ReportsPage() {
  const { t } = useTranslation();
  const can = useCan();
  const { enqueueSnackbar } = useSnackbar();
  const [groupBy, setGroupBy] = useState<ReportGroupBy>('client');
  const [range, setRange] = useState(() => presetRange('30'));
  const valid = range.from !== '' && range.to !== '' && range.from <= range.to;
  const params: ReportParams = { group_by: groupBy, date_from: range.from, date_to: range.to };

  const report = useQuery({
    queryKey: qk.admin.reports(params),
    queryFn: () => reportsApi.sales(params),
    enabled: valid,
    placeholderData: keepPreviousData,
  });

  const exportXlsx = useMutation({
    mutationFn: () => reportsApi.salesXlsx(params),
    onSuccess: (blob) => saveBlob(blob, `sales_${groupBy}_${range.from}_${range.to}.xlsx`),
    onError: () => enqueueSnackbar(t('reports.exportFailed'), { variant: 'error' }),
  });

  const data = report.data;
  const totalSum = data?.totals.sum ?? 0;

  return (
    <>
      <PageHeader
        title={t('reports.title')}
        subtitle={t('reports.subtitle')}
        actions={
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={() => exportXlsx.mutate()}
            disabled={!valid || exportXlsx.isPending || !can('reports.read')}
          >
            {t('reports.export')}
          </Button>
        }
      />
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems={{ md: 'center' }}>
          <TextField
            select
            size="small"
            label={t('reports.groupBy')}
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as ReportGroupBy)}
            sx={{ minWidth: 200 }}
          >
            {GROUPS.map((g) => (
              <MenuItem key={g} value={g}>
                {t(`reports.groups.${g}`)}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            type="date"
            label={t('reports.from')}
            value={range.from}
            onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
            InputLabelProps={{ shrink: true }}
            error={!valid}
          />
          <TextField
            size="small"
            type="date"
            label={t('reports.to')}
            value={range.to}
            onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
            InputLabelProps={{ shrink: true }}
            error={!valid}
          />
          <ToggleButtonGroup
            size="small"
            exclusive
            onChange={(_, p: Preset | null) => p && setRange(presetRange(p))}
          >
            <ToggleButton value="today">{t('reports.presetToday')}</ToggleButton>
            <ToggleButton value="7">{t('reports.preset7')}</ToggleButton>
            <ToggleButton value="30">{t('reports.preset30')}</ToggleButton>
            <ToggleButton value="month">{t('reports.presetMonth')}</ToggleButton>
          </ToggleButtonGroup>
        </Stack>
      </Paper>
      <Paper variant="outlined">
        {report.isFetching && <LinearProgress />}
        {report.isError ? (
          <ErrorState error={report.error} onRetry={() => void report.refetch()} />
        ) : (
          <TableContainer>
            <Table size="small" aria-label={t('reports.title')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('reports.colName')}</TableCell>
                  <TableCell align="right">{t('reports.colOrders')}</TableCell>
                  <TableCell align="right">{t('reports.colQty')}</TableCell>
                  <TableCell align="right">{t('reports.colSum')}</TableCell>
                  <TableCell align="right" sx={{ width: 160 }}>
                    {t('reports.colShare')}
                  </TableCell>
                </TableRow>
              </TableHead>
              {!data ? (
                <TableSkeleton cols={5} />
              ) : (
                <>
                  <TableBody>
                    {data.rows.map((r) => {
                      const share = totalSum > 0 ? (Number(r.sum) / Number(totalSum)) * 100 : 0;
                      return (
                        <TableRow key={`${r.key ?? ''}`} hover>
                          <TableCell>
                            {r.name ?? r.key ?? t('reports.noName')}
                            {groupBy !== 'day' && r.key && r.name && (
                              <Box component="span" sx={{ color: 'text.secondary', ml: 1 }}>
                                {r.key}
                              </Box>
                            )}
                          </TableCell>
                          <TableCell align="right">{r.orders}</TableCell>
                          <TableCell align="right">{formatAmount(r.qty)}</TableCell>
                          <TableCell align="right">{formatAmount(r.sum)}</TableCell>
                          <TableCell align="right">
                            <Stack direction="row" spacing={1} alignItems="center">
                              <LinearProgress
                                variant="determinate"
                                value={Math.min(100, share)}
                                sx={{ flex: 1, height: 6, borderRadius: 3 }}
                                aria-hidden
                              />
                              <Box component="span" sx={{ minWidth: 44 }}>
                                {share.toFixed(1)}%
                              </Box>
                            </Stack>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                  {data.rows.length > 0 && (
                    <TableFooter>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 700 }}>{t('reports.total')}</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>
                          {data.totals.orders}
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>
                          {formatAmount(data.totals.qty)}
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>
                          {formatAmount(data.totals.sum)}
                        </TableCell>
                        <TableCell />
                      </TableRow>
                    </TableFooter>
                  )}
                </>
              )}
            </Table>
          </TableContainer>
        )}
        {data && data.rows.length === 0 && <EmptyState title={t('reports.empty')} />}
        {data?.truncated && <Alert severity="info">{t('reports.truncated')}</Alert>}
      </Paper>
    </>
  );
}
