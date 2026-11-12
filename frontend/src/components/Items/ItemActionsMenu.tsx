import { EllipsisVertical } from "lucide-react"
import { useState } from "react"

import type { ItemPublic } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useAuth from "@/hooks/useAuth"
import DeleteItem from "../Items/DeleteItem"
import EditItem from "../Items/EditItem"
import ShareItem from "../Items/ShareItem"

interface ItemActionsMenuProps {
  item: ItemPublic
}

export const ItemActionsMenu = ({ item }: ItemActionsMenuProps) => {
  const [open, setOpen] = useState(false)
  const { user } = useAuth()
  const isOwner = item.owner_id === user?.id

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Item actions">
          <EllipsisVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <EditItem item={item} onSuccess={() => setOpen(false)} />
        {isOwner && (
          <>
            <ShareItem item={item} onClose={() => setOpen(false)} />
            <DeleteItem id={item.id} onSuccess={() => setOpen(false)} />
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
