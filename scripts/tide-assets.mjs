// Copy assets from the exact SDK version used by this build. The relay's CSP
// must match its inline scripts; never copy one without the other.
import { DPOP_AUTH_HTML, DPOP_AUTH_CSP } from '@tidecloak/js/dpop-server';
import { writeFileSync, copyFileSync } from 'node:fs';
writeFileSync('public/tide_dpop_auth.html', DPOP_AUTH_HTML);
writeFileSync('public/tide-dpop-csp.txt', DPOP_AUTH_CSP);
copyFileSync('node_modules/@tidecloak/js/silent-check-sso.html', 'public/silent-check-sso.html');
copyFileSync('node_modules/@tidecloak/js/LICENSE', 'public/licenses/Tide-Community-Open-Code.txt');
