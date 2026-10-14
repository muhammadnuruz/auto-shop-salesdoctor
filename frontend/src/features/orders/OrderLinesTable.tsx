import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { alpha, type Theme } from '@mui/material/styles';
import { memo, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import type { OrderLine, OrderProblem } from '@/api/types';
import { formatAmount } from '@/lib/format';
import { ProductThumb } from './ProductThumb';
import { QtyField } from './QtyField';
import { hasPack, problemText } from './orderUtils';
import {
  CARD_CHUNK,
  OVERSCAN,
  ROW_H,
  VIRTUALIZE_AFTER,
  filterLines,
  navigateDown,
} from './linesTableUtils';
import type { OptionalColumn } from './useOrderSettings';

export interface LinesTableProps {
  lines: OrderLine[];
  editable: boolean;
  columns: Record<OptionalColumn, boolean>;
  filter: string;
  /** product_ref -> problems (from the last submit attempt or the live order) */
  problems: Record<string, OrderProblem[]>;
  mobile: boolean;
  highlightRef?: string | null;
  onQty: (ref: string, patch: { blocks?: number; pieces?: number }) => void;
  onComment: (ref: string, comment: string) => void;
  onRemove: (ref: string) => void;
}

interface RowProps extends Omit<
  LinesTableProps,
  'lines' | 'filter' | 'columns' | 'mobile' | 'problems'
> {
  line: OrderLine;
  index: number;
  columns: Record<OptionalColumn, boolean>;
  rowProblems: OrderProblem[];
}

function useLineBits(line: OrderLine, rowProblems: OrderProblem[]) {
  const { t } = useTranslation();
  const over = line.warnings.includes('over_stock');
  const flags = line.warnings.filter((w) => w !== 'over_stock');
  const overText = t('order.overStockTip', {
    qty: formatAmount(line.quantity),
    stock: formatAmount(line.stock ?? 0),
  });
  const messages = rowProblems
    .filter((p) => !(p.code === 'over_stock' && over))
    .filter((p) => !flags.includes(p.code))
    .map((p) => problemText(t, p));
  return { t, over, flags, overText, messages };
}

function Flags({ flags, messages }: { flags: string[]; messages: string[] }) {
  const { t } = useTranslation();
  return (
    <>
      {flags.length > 0 && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} useFlexGap flexWrap="wrap">
          {flags.map((w) => (
            <Chip
              key={w}
              size="small"
              color="warning"
              variant="outlined"
              label={t(`order.warn_${w}`, { defaultValue: w })}
            />
          ))}
        </Stack>
      )}
      {messages.map((m) => (
        <Typography key={m} variant="caption" color="error" display="block" role="alert">
          {m}
        </Typography>
      ))}
    </>
  );
}

const overBg = (over: boolean, highlight: boolean) =>
  over
    ? (th: Theme) => alpha(th.palette.error.main, 0.12)
    : highlight
      ? (th: Theme) => alpha(th.palette.success.main, 0.14)
      : undefined;

function CommentField({
  line,
  disabled,
  onComment,
}: {
  line: OrderLine;
  disabled: boolean;
  onComment: (ref: string, v: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <TextField
      size="small"
      value={line.comment ?? ''}
      disabled={disabled}
      onChange={(e) => onComment(line.product_ref, e.target.value)}
      placeholder={t('order.colComment')}
      slotProps={{
        htmlInput: { maxLength: 500, 'aria-label': `${t('order.colComment')}: ${line.name}` },
      }}
      sx={{ minWidth: 140 }}
    />
  );
}

const TableRowView = memo(function TableRowView({
  line,
  index,
  columns,
  editable,
  rowProblems,
  highlightRef,
  onQty,
  onComment,
  onRemove,
}: RowProps) {
  const { t, over, flags, overText, messages } = useLineBits(line, rowProblems);
  const pack = hasPack(line.pack_quantity);
  return (
    <TableRow
      hover
      data-testid="order-row"
      data-ref={line.product_ref}
      data-over={over ? 'true' : undefined}
      sx={{ height: ROW_H, bgcolor: overBg(over, highlightRef === line.product_ref) }}
    >
      <TableCell>{index + 1}</TableCell>
      {columns.photo && (
        <TableCell>
          <ProductThumb name={line.name} thumbUrl={line.thumb_url} imageUrl={line.image_url} />
        </TableCell>
      )}
      <TableCell sx={{ minWidth: 200 }}>
        <Typography variant="body2" fontWeight={600}>
          {line.name}
        </Typography>
        {line.code && (
          <Typography variant="caption" color="text.secondary">
            {line.code}
          </Typography>
        )}
        <Flags flags={flags} messages={messages} />
      </TableCell>
      {columns.category && <TableCell>{line.category_name ?? '—'}</TableCell>}
      {columns.price && (
        <TableCell align="right">{!line.price ? '—' : formatAmount(line.price)}</TableCell>
      )}
      {columns.stock && (
        <TableCell align="right">
          {over ? (
            <Tooltip title={overText}>
              <Typography
                component="span"
                variant="body2"
                color="error"
                fontWeight={700}
                tabIndex={0}
              >
                {formatAmount(line.stock ?? 0)}
              </Typography>
            </Tooltip>
          ) : line.stock == null ? (
            '—'
          ) : (
            formatAmount(line.stock)
          )}
        </TableCell>
      )}
      {columns.blocks && (
        <TableCell>
          <QtyField
            integer
            label={`${t('order.colBlocks')}: ${line.name}`}
            value={line.blocks}
            disabled={!editable || !pack}
            navCol="blocks"
            navRow={index}
            onChange={(blocks) => onQty(line.product_ref, { blocks })}
          />
          {pack && (
            <Typography variant="caption" color="text.secondary" display="block">
              {t('order.perBlock', { n: formatAmount(line.pack_quantity) })}
            </Typography>
          )}
        </TableCell>
      )}
      {columns.pieces && (
        <TableCell>
          <QtyField
            label={`${t('order.colPieces')}: ${line.name}`}
            value={line.pieces}
            disabled={!editable}
            navCol="pieces"
            navRow={index}
            onChange={(pieces) => onQty(line.product_ref, { pieces })}
          />
        </TableCell>
      )}
      {columns.sum && (
        <TableCell align="right" sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
          {formatAmount(line.line_sum)}
        </TableCell>
      )}
      {columns.comment && (
        <TableCell>
          <CommentField line={line} disabled={!editable} onComment={onComment} />
        </TableCell>
      )}
      <TableCell padding="checkbox">
        {editable && (
          <Tooltip title={t('order.removeLine')}>
            <IconButton
              size="small"
              aria-label={`${t('order.removeLine')}: ${line.name}`}
              onClick={() => onRemove(line.product_ref)}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </TableCell>
    </TableRow>
  );
});

const CardView = memo(function CardView({
  line,
  columns,
  editable,
  rowProblems,
  highlightRef,
  onQty,
  onComment,
  onRemove,
}: RowProps) {
  const { t, over, flags, overText, messages } = useLineBits(line, rowProblems);
  const pack = hasPack(line.pack_quantity);
  const [showComment, setShowComment] = useState(!!line.comment);
  return (
    <Card
      variant="outlined"
      data-testid="order-card"
      data-ref={line.product_ref}
      data-over={over ? 'true' : undefined}
      sx={{ p: 1.5, bgcolor: overBg(over, highlightRef === line.product_ref) }}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        {columns.photo && (
          <ProductThumb
            name={line.name}
            thumbUrl={line.thumb_url}
            imageUrl={line.image_url}
            size={56}
          />
        )}
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="body2" fontWeight={600}>
            {line.name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {[
              columns.category ? line.category_name : null,
              columns.price && line.price != null ? formatAmount(line.price) : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </Typography>
          {columns.stock && (
            <Typography
              variant="caption"
              display="block"
              color={over ? 'error' : 'text.secondary'}
              fontWeight={over ? 700 : undefined}
            >
              {over ? overText : `${t('order.colStock')}: ${formatAmount(line.stock ?? 0)}`}
            </Typography>
          )}
          <Flags flags={flags} messages={messages} />
        </Box>
        {editable && (
          <IconButton
            aria-label={`${t('order.removeLine')}: ${line.name}`}
            onClick={() => onRemove(line.product_ref)}
          >
            <DeleteOutlineIcon />
          </IconButton>
        )}
      </Stack>
      <Stack
        direction="row"
        spacing={1}
        useFlexGap
        flexWrap="wrap"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mt: 1 }}
      >
        {columns.blocks && pack && (
          <QtyField
            big
            integer
            label={`${t('order.colBlocks')}: ${line.name}`}
            value={line.blocks}
            disabled={!editable}
            onChange={(blocks) => onQty(line.product_ref, { blocks })}
          />
        )}
        {columns.pieces && (
          <QtyField
            big
            label={`${t('order.colPieces')}: ${line.name}`}
            value={line.pieces}
            disabled={!editable}
            onChange={(pieces) => onQty(line.product_ref, { pieces })}
          />
        )}
        {columns.sum && (
          <Typography fontWeight={700} sx={{ ml: 'auto' }}>
            {formatAmount(line.line_sum)}
          </Typography>
        )}
      </Stack>
      {columns.comment &&
        (showComment || line.comment ? (
          <Box sx={{ mt: 1 }}>
            <CommentField line={line} disabled={!editable} onComment={onComment} />
          </Box>
        ) : (
          editable && (
            <Button size="small" sx={{ mt: 0.5 }} onClick={() => setShowComment(true)}>
              {t('order.addComment')}
            </Button>
          )
        ))}
    </Card>
  );
});

export function OrderLinesTable(props: LinesTableProps) {
  const { t } = useTranslation();
  const { lines, filter, columns, mobile, problems, ...rest } = props;
  const rows = filterLines(lines, filter);
  const [scrollTop, setScrollTop] = useState(0);
  const [chunk, setChunk] = useState(CARD_CHUNK);
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  const rootRef = { current: root } as RefObject<HTMLElement | null>;

  if (rows.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 4, textAlign: 'center' }}>
        {lines.length === 0 ? t('order.emptyLines') : t('order.noMatches')}
      </Typography>
    );
  }

  if (mobile) {
    const shown = rows.slice(0, chunk);
    return (
      <Stack spacing={1} ref={setRoot} onKeyDown={(e) => navigateDown(e, rootRef)}>
        {shown.map((line) => (
          <CardView
            key={line.product_ref}
            line={line}
            index={0}
            columns={columns}
            rowProblems={problems[line.product_ref] ?? []}
            {...rest}
          />
        ))}
        {rows.length > shown.length && (
          <Button onClick={() => setChunk((c) => c + CARD_CHUNK)}>
            {t('order.showMore', { n: rows.length - shown.length })}
          </Button>
        )}
      </Stack>
    );
  }

  const virtual = rows.length > VIRTUALIZE_AFTER;
  const viewportRows = 14;
  const start = virtual ? Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN) : 0;
  const end = virtual ? Math.min(rows.length, start + viewportRows + OVERSCAN * 2) : rows.length;
  const colCount = 3 + countVisible(columns);

  return (
    <TableContainer
      ref={setRoot}
      data-virtual={virtual ? 'true' : undefined}
      onScroll={virtual ? (e) => setScrollTop(e.currentTarget.scrollTop) : undefined}
      onKeyDown={(e) => navigateDown(e, rootRef)}
      sx={virtual ? { maxHeight: '70vh' } : undefined}
    >
      <Table size="small" stickyHeader aria-label={t('order.linesTable')}>
        <TableHead>
          <TableRow>
            <TableCell>#</TableCell>
            {columns.photo && <TableCell>{t('order.colPhoto')}</TableCell>}
            <TableCell>{t('order.colProduct')}</TableCell>
            {columns.category && <TableCell>{t('order.colCategory')}</TableCell>}
            {columns.price && <TableCell align="right">{t('order.colPrice')}</TableCell>}
            {columns.stock && <TableCell align="right">{t('order.colStock')}</TableCell>}
            {columns.blocks && <TableCell>{t('order.colBlocks')}</TableCell>}
            {columns.pieces && <TableCell>{t('order.colPieces')}</TableCell>}
            {columns.sum && <TableCell align="right">{t('order.colSum')}</TableCell>}
            {columns.comment && <TableCell>{t('order.colComment')}</TableCell>}
            <TableCell padding="checkbox">
              <span style={{ position: 'absolute', left: -9999 }}>{t('order.removeLine')}</span>
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {virtual && start > 0 && (
            <TableRow aria-hidden style={{ height: start * ROW_H }}>
              <TableCell colSpan={colCount} sx={{ p: 0, border: 0 }} />
            </TableRow>
          )}
          {rows.slice(start, end).map((line, i) => (
            <TableRowView
              key={line.product_ref}
              line={line}
              index={start + i}
              columns={columns}
              rowProblems={problems[line.product_ref] ?? []}
              {...rest}
            />
          ))}
          {virtual && end < rows.length && (
            <TableRow aria-hidden style={{ height: (rows.length - end) * ROW_H }}>
              <TableCell colSpan={colCount} sx={{ p: 0, border: 0 }} />
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function countVisible(columns: Record<OptionalColumn, boolean>): number {
  return Object.values(columns).filter(Boolean).length;
}
