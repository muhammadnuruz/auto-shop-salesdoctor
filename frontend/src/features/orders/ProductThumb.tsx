import ImageNotSupportedIcon from '@mui/icons-material/ImageNotSupported';
import Box from '@mui/material/Box';
import ButtonBase from '@mui/material/ButtonBase';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  name: string | null;
  thumbUrl: string | null;
  imageUrl: string | null;
  size?: number;
  /** fill the parent's width as a square (card view); prefers the full-size image */
  fill?: boolean;
}

/** Product photo thumbnail; click opens the full-size image. */
export function ProductThumb({
  name: nameOrNull,
  thumbUrl,
  imageUrl,
  size = 44,
  fill = false,
}: Props) {
  const { t } = useTranslation();
  const name = nameOrNull ?? '';
  const [open, setOpen] = useState(false);
  const [broken, setBroken] = useState(false);
  const src = fill ? (imageUrl ?? thumbUrl) : (thumbUrl ?? imageUrl);
  const frame = {
    width: fill ? '100%' : size,
    height: fill ? 'auto' : size,
    aspectRatio: '1 / 1',
    borderRadius: 1,
    border: 1,
    borderColor: 'divider',
    display: 'grid',
    placeItems: 'center',
    overflow: 'hidden',
    flexShrink: 0,
    bgcolor: 'action.hover',
  } as const;
  if (!src || broken) {
    return (
      <Box sx={frame} aria-hidden>
        <ImageNotSupportedIcon color="disabled" fontSize={fill ? 'large' : 'small'} />
      </Box>
    );
  }
  return (
    <>
      <ButtonBase
        onClick={() => setOpen(true)}
        aria-label={`${t('order.enlargePhoto')}: ${name}`}
        sx={frame}
      >
        <Box
          component="img"
          src={src}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
          sx={{ width: '100%', height: '100%', objectFit: fill ? 'contain' : 'cover' }}
        />
      </ButtonBase>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ fontSize: '1rem' }}>{name}</DialogTitle>
        <DialogContent>
          <Box
            component="img"
            src={imageUrl ?? src}
            alt={name}
            referrerPolicy="no-referrer"
            sx={{ width: '100%', maxHeight: '70vh', objectFit: 'contain' }}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
