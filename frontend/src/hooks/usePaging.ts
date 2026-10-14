import { useState } from 'react';

export function usePaging(initialSize = 25) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(initialSize);
  return {
    page,
    pageSize,
    offset: page * pageSize,
    setPage,
    setPageSize: (n: number) => {
      setPageSize(n);
      setPage(0);
    },
    reset: () => setPage(0),
  };
}
