import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import { useTranslation } from 'react-i18next';
import type { CategorySummaryRow, OrderTotals } from '@/api/types';
import { formatAmount } from '@/lib/format';

/** "Категория | Кол-во | Объём | Сумма" with an "Итого" row. */
export function CategorySummary({
  rows,
  totals,
}: {
  rows: CategorySummaryRow[];
  totals: OrderTotals;
}) {
  const { t } = useTranslation();
  return (
    <TableContainer sx={{ maxHeight: 220 }}>
      <Table size="small" stickyHeader aria-label={t('order.categorySummary')}>
        <TableHead>
          <TableRow>
            <TableCell>{t('order.colCategory')}</TableCell>
            <TableCell align="right">{t('order.colQty')}</TableCell>
            <TableCell align="right">{t('order.colVolume')}</TableCell>
            <TableCell align="right">{t('order.colSum')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.category ?? '—'}>
              <TableCell>{r.name}</TableCell>
              <TableCell align="right">{formatAmount(r.qty)}</TableCell>
              <TableCell align="right">{formatAmount(r.volume)}</TableCell>
              <TableCell align="right">{formatAmount(r.sum)}</TableCell>
            </TableRow>
          ))}
          <TableRow sx={{ '& td': { fontWeight: 700 } }} data-testid="summary-total">
            <TableCell>{t('order.total')}</TableCell>
            <TableCell align="right">{formatAmount(totals.qty)}</TableCell>
            <TableCell align="right">{formatAmount(totals.volume)}</TableCell>
            <TableCell align="right">{formatAmount(totals.sum)}</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </TableContainer>
  );
}
