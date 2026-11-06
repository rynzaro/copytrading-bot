import type { Metadata, Viewport } from 'next'
import '@near-wallet-selector/modal-ui/styles.css'
import './globals.css'
import { DisclaimerGate, Footer } from '@/components/disclaimer'

export const metadata: Metadata = { title: 'Copytrade', description: 'Copy NEAR meme buys from a vault only you control.' }
export const viewport: Viewport = { themeColor: '#091d22' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        {children}
        <Footer />
        <DisclaimerGate />
      </body>
    </html>
  )
}
