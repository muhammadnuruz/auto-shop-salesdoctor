import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useLocation, useNavigate, useParams } from 'react-router-dom';
import { linksApi } from '@/api/admin';
import { qk } from '@/api/keys';
import { PageHeader } from '@/components/PageHeader';
import { ErrorState } from '@/components/States';
import { useCan } from '../AdminContext';
import { LinkClientsPanel } from './LinkClientsPanel';
import { LinkForm } from './LinkForm';
import { formToCreate, formToUpdate, type LinkFormValues } from './schema';
import { LinkStatsPanel } from './LinkStatsPanel';
import { SharePanel } from './SharePanel';
import { stateColor } from './utils';

export function LinkCreatePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const create = useMutation({
    mutationFn: (v: LinkFormValues) => linksApi.create(formToCreate(v)),
    meta: { silent: true }, // 409/422 are shown inline by the form
  });

  return (
    <>
      <PageHeader
        title={t('links.createTitle')}
        actions={
          <Button component={RouterLink} to="/admin/links" startIcon={<ArrowBackIcon />}>
            {t('links.backToList')}
          </Button>
        }
      />
      <Box sx={{ maxWidth: 900 }}>
        <LinkForm
          submitLabel={t('common.create')}
          onSubmit={async (values) => {
            const link = await create.mutateAsync(values);
            await qc.invalidateQueries({ queryKey: qk.admin.links.all() });
            enqueueSnackbar(t('links.created'), { variant: 'success' });
            navigate(`/admin/links/${link.id}`, {
              replace: true,
              state: { warnings: link.warnings ?? [] },
            });
          }}
        />
      </Box>
    </>
  );
}

export function LinkEditPage() {
  const { t } = useTranslation();
  const id = Number(useParams().id);
  const location = useLocation();
  const qc = useQueryClient();
  const can = useCan();
  const { enqueueSnackbar } = useSnackbar();
  const warnings = (location.state as { warnings?: string[] } | null)?.warnings;
  const link = useQuery({
    queryKey: qk.admin.links.detail(id),
    queryFn: () => linksApi.get(id),
    enabled: Number.isFinite(id),
  });
  const canWrite = can('links.write');

  const update = useMutation({
    mutationFn: (v: LinkFormValues) => linksApi.update(id, formToUpdate(v)),
    meta: { silent: true },
  });

  if (link.isPending) return <Skeleton variant="rounded" height={400} />;
  if (link.isError) return <ErrorState error={link.error} onRetry={() => void link.refetch()} />;
  const data = link.data;

  return (
    <>
      <PageHeader
        title={t('links.editTitle', { name: data.name })}
        actions={
          <>
            <Chip color={stateColor(data.state)} label={t(`links.state_${data.state}`)} />
            <Button component={RouterLink} to="/admin/links" startIcon={<ArrowBackIcon />}>
              {t('links.backToList')}
            </Button>
          </>
        }
      />
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) 360px' },
          gap: 2,
          alignItems: 'start',
        }}
      >
        {canWrite ? (
          <LinkForm
            key={data.updated_at}
            link={data}
            submitLabel={t('common.save')}
            onSubmit={async (values) => {
              const saved = await update.mutateAsync(values);
              qc.setQueryData(qk.admin.links.detail(id), saved);
              await qc.invalidateQueries({ queryKey: qk.admin.links.all() });
              enqueueSnackbar(t('links.updated'), { variant: 'success' });
            }}
          />
        ) : (
          <Alert severity="info">{t('errors.forbidden')}</Alert>
        )}
        <Stack spacing={2}>
          <SharePanel link={data} warnings={warnings} />
          <LinkStatsPanel linkId={id} />
          {data.client_scope === 'selected' && <LinkClientsPanel linkId={id} canWrite={canWrite} />}
        </Stack>
      </Box>
    </>
  );
}
