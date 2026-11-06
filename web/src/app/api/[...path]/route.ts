import { getApi } from '@/server/context'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const handle = (req: Request) => getApi()(req)
export { handle as GET, handle as POST, handle as PUT, handle as DELETE }
