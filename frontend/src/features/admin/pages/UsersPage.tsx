import { zodResolver } from '@hookform/resolvers/zod';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import LockResetIcon from '@mui/icons-material/LockReset';
import LogoutIcon from '@mui/icons-material/Logout';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormGroup from '@mui/material/FormGroup';
import FormLabel from '@mui/material/FormLabel';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useMemo, useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';
import { usersApi } from '@/api/admin';
import { ApiError } from '@/api/http';
import { qk } from '@/api/keys';
import type { AdminUser, Role } from '@/api/types';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { CopyButton } from '@/components/CopyButton';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { usePaging } from '@/hooks/usePaging';
import { formatDateTime } from '@/lib/format';
import {
  ALL_PERMISSIONS,
  ROLE_PERMISSIONS,
  canManageTarget,
  grantableRoles,
} from '@/lib/permissions';
import { useCurrentAdmin } from '../AdminContext';

interface UserFormValues {
  username: string;
  full_name: string;
  role: Role;
  extra_permissions: string[];
  password: string;
  active: boolean;
}

function TempPasswordDialog({
  password,
  onClose,
}: {
  password: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={!!password} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>{t('users.tempPasswordTitle')}</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Alert severity="warning">{t('users.tempPasswordText')}</Alert>
          <Stack direction="row" alignItems="center" spacing={1}>
            <TextField
              label={t('users.tempPassword')}
              value={password ?? ''}
              slotProps={{ input: { readOnly: true, sx: { fontFamily: 'monospace' } } }}
            />
            <CopyButton value={password ?? ''} />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button variant="contained" onClick={onClose}>
          {t('common.done')}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function UserDialog({
  target,
  open,
  onClose,
  onTempPassword,
}: {
  target: AdminUser | null;
  open: boolean;
  onClose: () => void;
  onTempPassword: (p: string) => void;
}) {
  const { t } = useTranslation();
  const actor = useCurrentAdmin();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const isEdit = !!target;
  const roles = grantableRoles(actor);
  const grantable =
    actor.role === 'superadmin'
      ? ALL_PERMISSIONS
      : ALL_PERMISSIONS.filter((p) => actor.permissions.includes(p));
  const isSelf = target?.id === actor.id;

  const schema = useMemo(
    () =>
      z.object({
        username: isEdit
          ? z.string()
          : z.string().trim().min(3, t('users.usernameInvalid')).max(100),
        full_name: z.string().max(200),
        role: z.enum(['superadmin', 'admin', 'operator', 'viewer']),
        extra_permissions: z.array(z.string()),
        password: z.string().refine((s) => s === '' || s.length >= 12, t('adminAuth.passwordMin')),
        active: z.boolean(),
      }),
    [t, isEdit],
  );
  const form = useForm<UserFormValues>({
    resolver: zodResolver(schema),
    values: {
      username: target?.username ?? '',
      full_name: target?.full_name ?? '',
      role: target?.role ?? roles[roles.length - 1] ?? 'viewer',
      extra_permissions: target?.extra_permissions ?? [],
      password: '',
      active: target?.active ?? true,
    },
  });
  const { errors } = form.formState;
  const role = useWatch({ control: form.control, name: 'role' });

  const save = useMutation({
    meta: { silent: true },
    mutationFn: async (v: UserFormValues): Promise<AdminUser & { temporary_password?: string }> => {
      const extra = v.extra_permissions.filter((p) => !ROLE_PERMISSIONS[v.role].includes(p));
      if (target) {
        return usersApi.update(target.id, {
          full_name: v.full_name,
          role: v.role,
          extra_permissions: extra,
          ...(isSelf ? {} : { active: v.active }),
        });
      }
      return usersApi.create({
        username: v.username.trim(),
        full_name: v.full_name,
        role: v.role,
        extra_permissions: extra,
        password: v.password || null,
      });
    },
    onSuccess: async (res) => {
      enqueueSnackbar(t(isEdit ? 'users.updated' : 'users.created'), { variant: 'success' });
      await qc.invalidateQueries({ queryKey: qk.admin.users.all() });
      onClose();
      if (res.temporary_password) onTempPassword(res.temporary_password);
    },
    onError: (err) => {
      if (err instanceof ApiError && (err.status === 409 || err.status === 422)) {
        const msg = err.issues.map((i) => i.message).join('; ');
        form.setError(err.status === 409 ? 'username' : 'password', { message: msg });
        return;
      }
      enqueueSnackbar(
        err instanceof ApiError ? err.issues.map((i) => i.message).join('; ') : t('errors.unknown'),
        { variant: 'error' },
      );
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="user-dialog-title"
    >
      <form noValidate onSubmit={form.handleSubmit((v) => save.mutate(v))}>
        <DialogTitle id="user-dialog-title">
          {isEdit ? t('users.editTitle', { name: target.username }) : t('users.createTitle')}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {!isEdit && (
              <TextField
                {...form.register('username')}
                label={t('users.username')}
                error={!!errors.username}
                helperText={errors.username?.message}
                autoComplete="off"
                slotProps={{ htmlInput: { autoCapitalize: 'off' } }}
              />
            )}
            <TextField {...form.register('full_name')} label={t('users.fullName')} />
            <Controller
              control={form.control}
              name="role"
              render={({ field }) => (
                <TextField select label={t('users.role')} {...field}>
                  {(roles.includes(field.value) ? roles : [...roles, field.value]).map((r) => (
                    <MenuItem key={r} value={r}>
                      {t(`users.roles.${r}`)}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            />
            <Controller
              control={form.control}
              name="extra_permissions"
              render={({ field }) => (
                <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                  <FormLabel component="legend">{t('users.extraPermissions')}</FormLabel>
                  <FormGroup
                    sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}
                  >
                    {grantable.map((p) => {
                      const fromRole = ROLE_PERMISSIONS[role].includes(p);
                      return (
                        <FormControlLabel
                          key={p}
                          label={p}
                          control={
                            <Checkbox
                              size="small"
                              checked={fromRole || field.value.includes(p)}
                              disabled={fromRole}
                              onChange={(_, c) =>
                                field.onChange(
                                  c ? [...field.value, p] : field.value.filter((x) => x !== p),
                                )
                              }
                            />
                          }
                        />
                      );
                    })}
                  </FormGroup>
                </fieldset>
              )}
            />
            {!isEdit && (
              <TextField
                {...form.register('password')}
                type="password"
                autoComplete="new-password"
                label={t('users.passwordOptional')}
                error={!!errors.password}
                helperText={errors.password?.message}
              />
            )}
            {isEdit && !isSelf && (
              <Controller
                control={form.control}
                name="active"
                render={({ field }) => (
                  <FormControlLabel
                    control={
                      <Switch checked={field.value} onChange={(_, v) => field.onChange(v)} />
                    }
                    label={t('users.active')}
                  />
                )}
              />
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={save.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={save.isPending}>
            {isEdit ? t('common.save') : t('common.create')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

type Confirm = { kind: 'reset' | 'revoke' | 'delete'; user: AdminUser } | null;

export default function UsersPage() {
  const { t } = useTranslation();
  const actor = useCurrentAdmin();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const paging = usePaging(50);
  const [dialog, setDialog] = useState<{ user: AdminUser | null } | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [temp, setTemp] = useState<string | null>(null);

  const params = { limit: paging.pageSize, offset: paging.offset };
  const list = useQuery({
    queryKey: qk.admin.users.list(params),
    queryFn: () => usersApi.list(params),
    placeholderData: keepPreviousData,
  });

  const act = useMutation({
    mutationFn: async ({ kind, user }: NonNullable<Confirm>) => {
      if (kind === 'reset') return { kind, res: await usersApi.resetPassword(user.id) };
      if (kind === 'revoke') return { kind, res: await usersApi.revokeSessions(user.id) };
      await usersApi.remove(user.id);
      return { kind, res: null };
    },
    onSuccess: async ({ kind, res }) => {
      if (kind === 'reset' && res && 'temporary_password' in res) setTemp(res.temporary_password);
      if (kind === 'revoke' && res && 'revoked' in res)
        enqueueSnackbar(t('users.revokedOk', { count: res.revoked }), { variant: 'success' });
      if (kind === 'delete') enqueueSnackbar(t('users.deleted'), { variant: 'success' });
      setConfirm(null);
      await qc.invalidateQueries({ queryKey: qk.admin.users.all() });
    },
  });

  const items = list.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={t('users.title')}
        actions={
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setDialog({ user: null })}
          >
            {t('users.new')}
          </Button>
        }
      />
      <Paper variant="outlined">
        <TableContainer>
          <Table size="small" aria-label={t('users.title')}>
            <TableHead>
              <TableRow>
                <TableCell>{t('users.colUser')}</TableCell>
                <TableCell>{t('users.colRole')}</TableCell>
                <TableCell>{t('users.colStatus')}</TableCell>
                <TableCell>{t('users.colTotp')}</TableCell>
                <TableCell>{t('users.colLastLogin')}</TableCell>
                <TableCell align="right">{t('common.actions')}</TableCell>
              </TableRow>
            </TableHead>
            {list.isPending ? (
              <TableSkeleton cols={6} />
            ) : (
              <TableBody>
                {items.map((u) => {
                  const self = u.id === actor.id;
                  const manageable = canManageTarget(actor, u);
                  return (
                    <TableRow key={u.id} hover>
                      <TableCell>
                        <Typography fontWeight={600}>
                          {u.username}{' '}
                          {self && <Chip size="small" label={t('users.you')} sx={{ ml: 0.5 }} />}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {u.full_name}
                        </Typography>
                      </TableCell>
                      <TableCell>{t(`users.roles.${u.role}`)}</TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          color={u.active ? 'success' : 'default'}
                          label={u.active ? t('users.active') : t('users.inactive')}
                        />
                      </TableCell>
                      <TableCell>{u.totp_enabled ? t('common.yes') : t('common.no')}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {formatDateTime(u.last_login_at)}
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {manageable && (
                          <>
                            <Tooltip title={t('common.edit')}>
                              <IconButton
                                size="small"
                                aria-label={`${t('common.edit')}: ${u.username}`}
                                onClick={() => setDialog({ user: u })}
                              >
                                <EditIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            {!self && (
                              <Tooltip title={t('users.resetPassword')}>
                                <IconButton
                                  size="small"
                                  aria-label={`${t('users.resetPassword')}: ${u.username}`}
                                  onClick={() => setConfirm({ kind: 'reset', user: u })}
                                >
                                  <LockResetIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            <Tooltip title={t('users.revoke')}>
                              <IconButton
                                size="small"
                                aria-label={`${t('users.revoke')}: ${u.username}`}
                                onClick={() => setConfirm({ kind: 'revoke', user: u })}
                              >
                                <LogoutIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            {!self && (
                              <Tooltip title={t('common.delete')}>
                                <IconButton
                                  size="small"
                                  color="error"
                                  aria-label={`${t('common.delete')}: ${u.username}`}
                                  onClick={() => setConfirm({ kind: 'delete', user: u })}
                                >
                                  <DeleteIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                          </>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            )}
          </Table>
        </TableContainer>
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && <EmptyState title={t('users.empty')} />}
        <TablePagination
          component="div"
          count={list.data?.total ?? 0}
          page={paging.page}
          rowsPerPage={paging.pageSize}
          onPageChange={(_, p) => paging.setPage(p)}
          onRowsPerPageChange={(e) => paging.setPageSize(Number(e.target.value))}
          rowsPerPageOptions={[25, 50, 100]}
          labelRowsPerPage={t('common.rowsPerPage')}
          labelDisplayedRows={({ from, to, count }) => `${from}–${to} ${t('common.of')} ${count}`}
        />
      </Paper>

      {dialog && (
        <UserDialog
          open
          target={dialog.user}
          onClose={() => setDialog(null)}
          onTempPassword={setTemp}
        />
      )}
      <TempPasswordDialog password={temp} onClose={() => setTemp(null)} />
      <ConfirmDialog
        open={!!confirm}
        destructive={confirm?.kind !== 'reset'}
        loading={act.isPending}
        title={
          confirm
            ? t(
                confirm.kind === 'reset'
                  ? 'users.resetPassword'
                  : confirm.kind === 'revoke'
                    ? 'users.revoke'
                    : 'common.delete',
              )
            : ''
        }
        message={
          confirm
            ? t(
                confirm.kind === 'reset'
                  ? 'users.resetConfirm'
                  : confirm.kind === 'revoke'
                    ? 'users.revokeConfirm'
                    : 'users.deleteConfirm',
                { name: confirm.user.username },
              )
            : undefined
        }
        onClose={() => setConfirm(null)}
        onConfirm={() => confirm && act.mutate(confirm)}
      />
    </>
  );
}
