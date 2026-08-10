import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';

import { AppComponent } from './app/app.component';
import { APP_ROUTES } from './app/app.routes';
import { csrfInterceptor } from './app/core/csrf.interceptor';

bootstrapApplication(AppComponent, {
  providers: [
    provideRouter(APP_ROUTES),
    provideHttpClient(withInterceptors([csrfInterceptor])),
  ],
}).catch((err) => console.error(err));