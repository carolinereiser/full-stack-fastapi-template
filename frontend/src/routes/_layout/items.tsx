import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { Suspense } from "react"

import { ItemsService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import AddItem from "@/components/Items/AddItem"
import { columns } from "@/components/Items/columns"
import PendingItems from "@/components/Pending/PendingItems"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

function getItemsQueryOptions(shared: boolean) {
  return {
    queryFn: async () =>
      (
        await (shared
          ? ItemsService.readSharedItems({ query: { skip: 0, limit: 100 } })
          : ItemsService.readItems({ query: { skip: 0, limit: 100 } }))
      ).data,
    queryKey: shared ? ["shared-items"] : ["items"],
  }
}

export const Route = createFileRoute("/_layout/items")({
  component: Items,
  head: () => ({
    meta: [
      {
        title: "Items - FastAPI Template",
      },
    ],
  }),
})

function ItemsTableContent({ shared }: { shared: boolean }) {
  const { data: items } = useSuspenseQuery(getItemsQueryOptions(shared))

  if (items.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <Search className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">
          {shared
            ? "No items have been shared with you yet"
            : "You don't have any items yet"}
        </h3>
        <p className="text-muted-foreground">
          {shared
            ? "Items others share with you will appear here"
            : "Add a new item to get started"}
        </p>
      </div>
    )
  }

  return <DataTable columns={columns} data={items.data} />
}

function ItemsTable({ shared = false }: { shared?: boolean }) {
  return (
    <Suspense fallback={<PendingItems />}>
      <ItemsTableContent shared={shared} />
    </Suspense>
  )
}

function Items() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Items</h1>
          <p className="text-muted-foreground">Create and manage your items</p>
        </div>
        <AddItem />
      </div>
      <Tabs defaultValue="owned">
        <TabsList aria-label="Item lists">
          <TabsTrigger value="owned">My items</TabsTrigger>
          <TabsTrigger value="shared">Shared with me</TabsTrigger>
        </TabsList>
        <TabsContent value="owned">
          <ItemsTable />
        </TabsContent>
        <TabsContent value="shared">
          <ItemsTable shared />
        </TabsContent>
      </Tabs>
    </div>
  )
}
