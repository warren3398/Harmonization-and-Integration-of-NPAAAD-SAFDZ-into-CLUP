export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    try {
      const target = 'https://almed-pclam.onrender.com' + url.pathname + url.search;
      if (request.method === 'GET' || request.method === 'HEAD') {
        return await fetch(target, {method: request.method});
      }
      if (request.method === 'DELETE') return await fetch(target, {method: 'DELETE'});
      if (request.method === 'POST') {
        return await fetch(target, {method: 'POST', headers: {'content-type': 'application/json'},
          body: await request.text()});
      }
      return new Response(null, {status: 405});
    } catch (error) {
      console.error('PCLAM upstream request failed', error);
      return Response.json({detail: 'The processing service is temporarily unavailable.'}, {status: 502});
    }
  }
};
