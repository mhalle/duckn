import { defineConfig } from 'vite';

// CORS proxy plugin — forwards /cors-proxy/<encoded-url> to the target,
// relaying Range headers so byte-range ZMP chunk fetches work.
function corsProxy() {
  return {
    name: 'cors-proxy',
    configureServer(server) {
      server.middlewares.use('/cors-proxy/', async (req, res) => {
        // Handle CORS preflight
        if (req.method === 'OPTIONS') {
          res.writeHead(204, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
            'Access-Control-Allow-Headers': 'Range, If-None-Match, Accept',
            'Access-Control-Max-Age': '86400',
          });
          res.end();
          return;
        }

        const target = decodeURIComponent(req.url.slice(1)); // strip leading /
        if (!target.startsWith('http://') && !target.startsWith('https://')) {
          res.writeHead(400);
          res.end('Bad target URL');
          return;
        }

        const headers = {};
        if (req.headers.range) headers['Range'] = req.headers.range;
        if (req.headers.accept) headers['Accept'] = req.headers.accept;

        try {
          const upstream = await fetch(target, { headers });
          const relay = ['content-type', 'content-length', 'content-range', 'accept-ranges'];
          const outHeaders = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
          };
          for (const h of relay) {
            const v = upstream.headers.get(h);
            if (v) outHeaders[h] = v;
          }

          res.writeHead(upstream.status, outHeaders);

          if (upstream.body) {
            const reader = upstream.body.getReader();
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              res.write(value);
            }
            res.end();
          } else {
            res.end(Buffer.from(await upstream.arrayBuffer()));
          }
        } catch (err) {
          res.writeHead(502);
          res.end(err.message);
        }
      });
    },
  };
}

export default defineConfig({
  appType: 'mpa',
  plugins: [corsProxy()],
});
