import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import List from '@mui/material/List';
import ListItemAvatar from '@mui/material/ListItemAvatar';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import { useTranslation } from 'react-i18next';
import type { CatalogItem } from '@/api/types';
import { formatAmount } from '@/lib/format';
import { ProductThumb } from '@/features/orders/ProductThumb';

interface Props {
  code: string | null;
  candidates: CatalogItem[];
  onPick: (item: CatalogItem) => void;
  onClose: () => void;
}

/** The scanned barcode belongs to several products: let the user choose. */
export function AmbiguousDialog({ code, candidates, onPick, onClose }: Props) {
  const { t } = useTranslation();
  return (
    <Dialog open={code !== null} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{t('order.ambiguousTitle')}</DialogTitle>
      <DialogContent>
        <DialogContentText>{t('order.ambiguousText', { code })}</DialogContentText>
        <List>
          {candidates.map((c) => (
            <ListItemButton key={c.product_ref} onClick={() => onPick(c)}>
              <ListItemAvatar>
                <ProductThumb name={c.name} thumbUrl={c.thumb_url} imageUrl={null} />
              </ListItemAvatar>
              <ListItemText
                primary={c.name}
                secondary={[
                  c.category_name,
                  c.price != null ? formatAmount(c.price) : null,
                  c.stock != null ? `${t('order.colStock')}: ${formatAmount(c.stock)}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            </ListItemButton>
          ))}
        </List>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('common.cancel')}</Button>
      </DialogActions>
    </Dialog>
  );
}
