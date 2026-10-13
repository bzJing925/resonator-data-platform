import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { UploadProgressProvider } from '../../src/contexts/UploadProgressContext';
import UploadGroup from '../../src/upload/UploadGroup';
function Harness() {
  const [files, setFiles] = useState<File[]>([]), params = new URLSearchParams(window.location.search);
  return <><label>选择测试数据<input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} /></label><UploadGroup files={files} options={{ mapping_id: Number(params.get('mappingId')), f_start_ghz: 1, f_end_ghz: 5, process_type: 'AUTO', deembed: false, deembed_method: 'default', target_batch: '' }} /></>;
}
createRoot(document.getElementById('root')!).render(<MemoryRouter><UploadProgressProvider><Harness /></UploadProgressProvider></MemoryRouter>);
