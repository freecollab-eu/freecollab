import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { EmbedSignIn, isEmbedSignInRoute } from './components/EmbedSignIn'
import { NativeClientSignIn } from './components/NativeClientSignIn'
import { api } from './lib/api'
import { initializeMobile } from './mobile'
import { AccountProvider } from './account/AccountProvider'
import { AppearanceProvider } from './appearance/AppearanceProvider'
import { LocaleProvider } from './i18n'
import '@fontsource-variable/inter'
import '@fontsource-variable/manrope'
import '@fontsource-variable/source-serif-4'
import '@fontsource-variable/source-serif-4/wght-italic.css'
import './index.css'

const embeddedSignIn = isEmbedSignInRoute()
if (embeddedSignIn) api.useMemorySession()
else initializeMobile()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LocaleProvider><AppearanceProvider><AccountProvider>{embeddedSignIn ? <EmbedSignIn /> : <NativeClientSignIn><App /></NativeClientSignIn>}</AccountProvider></AppearanceProvider></LocaleProvider>
  </React.StrictMode>,
)
