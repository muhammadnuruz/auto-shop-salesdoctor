import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import HourglassTopIcon from '@mui/icons-material/HourglassTop';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardActionArea from '@mui/material/CardActionArea';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink } from 'react-router-dom';
import { dashboardApi } from '@/api/admin';
import { qk } from '@/api/keys';
import { ORDER_STATES, type Dashboard } from '@/api/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState } from '@/components/States';
import { stateColor } from '@/features/orders/orderUtils';
import { formatAmount, formatDateTime } from '@/lib/format';
import { useCan } from '../AdminContext';
import { NAV_ITEMS } from '../nav';
import { useCurrentAdmin } from '../AdminContext';

const PERIODS = ['today', '7d', '30d'] as const;

function Kpi({ label, orders, sum }: { label: string; orders: number; sum: number }) {
  const { t } = useTranslation();
  return (
    <Card variant="outlined" data-testid={`kpi-${label}`}>
      <CardContent>
        <Typography color="text.secondary">{t(`dash.period_${label}`)}</Typography>
        <Typography variant="h2" component="p" sx={{ mt: 0.5 }}>
          {formatAmount(sum)} {t('common.currency')}
        </Typography>
        <Typography color="text.secondary">{t('dash.ordersCount', { count: orders })}</Typography>
      </CardContent>
    </Card>
  );
}

function ProblemCard({
  count,
  state,
  icon,
  exhausted,
}: {
  count: number;
  state: 'failed' | 'outcome_unknown';
  icon: React.ReactNode;
  exhausted?: number;
}) {
  const { t } = useTranslation();
  return (
    <Card variant="outlined" sx={{ borderColor: count > 0 ? 'error.main' : undefined }}>
      <CardActionArea component={RouterLink} to={`/admin/orders?state=${state}`}>
        <CardContent>
          <Stack direction="row" spacing={2} alignItems="center">
            {icon}
            <Box>
              <Typography variant="h2" component="p" data-testid={`count-${state}`}>
                {count}
              </Typography>
              <Typography color="text.secondary">{t(`orderState.${state}`)}</Typography>
              {exhausted ? (
                <Typography variant="body2" color="error" data-testid="count-exhausted">
                  {t('dash.exhausted')}: {exhausted}
                </Typography>
              ) : null}
            </Box>
          </Stack>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="h3" component="h2" sx={{ mb: 1 }}>
        {title}
      </Typography>
      {children}
    </Paper>
  );
}

function DashboardBody({ d }: { d: Dashboard }) {
  const { t } = useTranslation();
  const cur = t('common.currency');
  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' },
        }}
      >
        {PERIODS.map((p) => (
          <Kpi key={p} label={p} orders={d.periods[p]?.orders ?? 0} sum={d.periods[p]?.sum ?? 0} />
        ))}
      </Box>

      <Box
        sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)' } }}
      >
        <ProblemCard
          count={d.failed}
          state="failed"
          icon={<ErrorOutlineIcon color="error" fontSize="large" />}
        />
        <ProblemCard
          count={d.outcome_unknown}
          state="outcome_unknown"
          exhausted={d.reconcile_exhausted}
          icon={<HourglassTopIcon color="warning" fontSize="large" />}
        />
      </Box>

      <Section title={t('dash.byState')}>
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
          {ORDER_STATES.filter((s) => d.by_state[s]).map((s) => (
            <Chip
              key={s}
              component={RouterLink}
              to={`/admin/orders?state=${s}`}
              clickable
              color={stateColor(s)}
              label={`${t(`orderState.${s}`)}: ${d.by_state[s]?.orders} · ${formatAmount(d.by_state[s]?.sum)}`}
            />
          ))}
          {Object.keys(d.by_state).length === 0 && (
            <Typography color="text.secondary">{t('dash.noData')}</Typography>
          )}
        </Stack>
      </Section>

      <Box
        sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'repeat(2, 1fr)' } }}
      >
        <Section title={t('dash.topClients')}>
          <TableContainer>
            <Table size="small" aria-label={t('dash.topClients')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('dash.client')}</TableCell>
                  <TableCell align="right">{t('dash.orders')}</TableCell>
                  <TableCell align="right">{t('dash.sum')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {d.top_clients.map((c) => (
                  <TableRow key={c.client_id} hover>
                    <TableCell>
                      <Button
                        component={RouterLink}
                        to={`/admin/orders?client=${c.client_id}`}
                        size="small"
                        sx={{ textAlign: 'left' }}
                      >
                        {c.name ?? c.client_id}
                      </Button>
                    </TableCell>
                    <TableCell align="right">{c.orders}</TableCell>
                    <TableCell align="right">
                      {formatAmount(c.sum)} {cur}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          {d.top_clients.length === 0 && <EmptyState title={t('dash.noData')} />}
        </Section>
        <Section title={t('dash.topProducts')}>
          <TableContainer>
            <Table size="small" aria-label={t('dash.topProducts')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('dash.product')}</TableCell>
                  <TableCell align="right">{t('dash.qty')}</TableCell>
                  <TableCell align="right">{t('dash.sum')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {d.top_products.map((p) => (
                  <TableRow key={p.product_ref} hover>
                    <TableCell>{p.name}</TableCell>
                    <TableCell align="right">{formatAmount(p.qty)}</TableCell>
                    <TableCell align="right">
                      {formatAmount(p.sum)} {cur}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          {d.top_products.length === 0 && <EmptyState title={t('dash.noData')} />}
        </Section>
        <Section title={t('dash.byLink')}>
          <TableContainer>
            <Table size="small" aria-label={t('dash.byLink')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('dash.link')}</TableCell>
                  <TableCell align="right">{t('dash.orders')}</TableCell>
                  <TableCell align="right">{t('dash.sum')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {d.by_link.map((l) => (
                  <TableRow key={l.link_id} hover>
                    <TableCell>
                      <Button
                        component={RouterLink}
                        to={`/admin/orders?link=${l.link_id}`}
                        size="small"
                        sx={{ textAlign: 'left' }}
                      >
                        {l.name}
                      </Button>
                    </TableCell>
                    <TableCell align="right">{l.orders}</TableCell>
                    <TableCell align="right">
                      {formatAmount(l.sum)} {cur}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          {d.by_link.length === 0 && <EmptyState title={t('dash.noData')} />}
        </Section>
        <Section title={t('dash.lastSync')}>
          <TableContainer>
            <Table size="small" aria-label={t('dash.lastSync')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('dash.entity')}</TableCell>
                  <TableCell>{t('common.status')}</TableCell>
                  <TableCell>{t('dash.finished')}</TableCell>
                  <TableCell align="right">{t('dash.items')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {Object.entries(d.last_sync).map(([entity, r]) => (
                  <TableRow key={entity}>
                    <TableCell>{entity}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={
                          r.status === 'success' || r.status === 'ok'
                            ? 'success'
                            : r.status === 'running'
                              ? 'info'
                              : 'error'
                        }
                        label={t(`sync.st_${r.status}`, { defaultValue: r.status })}
                      />
                    </TableCell>
                    <TableCell>{formatDateTime(r.finished_at ?? r.started_at)}</TableCell>
                    <TableCell align="right">{r.items}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Section>
      </Box>
    </Stack>
  );
}

/** Operators have no `reports.read`: show shortcuts instead of a forbidden screen. */
function Shortcuts() {
  const { t } = useTranslation();
  const user = useCurrentAdmin();
  const items = NAV_ITEMS.filter(
    (i) =>
      i.to !== '/admin' &&
      i.to !== '/admin/profile' &&
      (!i.perm || user.permissions.includes(i.perm)),
  );
  return (
    <Stack spacing={1.5} alignItems="flex-start">
      <Typography color="text.secondary">{t('dash.noReports')}</Typography>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        {items.map(({ to, labelKey, icon: Icon }) => (
          <Button key={to} component={RouterLink} to={to} variant="outlined" startIcon={<Icon />}>
            {t(labelKey)}
          </Button>
        ))}
      </Stack>
    </Stack>
  );
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const can = useCan();
  const allowed = can('reports.read');
  const q = useQuery({
    queryKey: qk.admin.dashboard(),
    queryFn: dashboardApi.get,
    enabled: allowed,
    refetchInterval: 60_000,
  });
  return (
    <>
      <PageHeader title={t('nav.dashboard')} />
      {!allowed ? (
        <Shortcuts />
      ) : q.isPending ? (
        <Skeleton variant="rounded" height={240} aria-busy="true" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <DashboardBody d={q.data} />
      )}
    </>
  );
}
