import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Share2 } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { type ItemPublic, ItemsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const formSchema = z.object({
  email: z.string().trim().email({ message: "Enter a valid email address" }),
})

type FormData = z.infer<typeof formSchema>

interface ShareItemProps {
  item: ItemPublic
  onClose: () => void
}

const ShareItem = ({ item, onClose }: ShareItemProps) => {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const sharesQueryKey = ["item-shares", item.id]

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    defaultValues: { email: "" },
  })

  const shares = useQuery({
    queryKey: sharesQueryKey,
    queryFn: async () =>
      (await ItemsService.readItemShares({ path: { id: item.id } })).data,
    enabled: isOpen,
  })

  const invalidateQueries = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: sharesQueryKey }),
      queryClient.invalidateQueries({ queryKey: ["items"] }),
      queryClient.invalidateQueries({ queryKey: ["shared-items"] }),
    ])

  const shareMutation = useMutation({
    mutationFn: (data: FormData) =>
      ItemsService.shareItem({ path: { id: item.id }, body: data }),
    onSuccess: () => {
      showSuccessToast("Item shared successfully")
      form.reset()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: invalidateQueries,
  })

  const revokeMutation = useMutation({
    mutationFn: (userId: string) =>
      ItemsService.revokeItemShare({
        path: { id: item.id, user_id: userId },
      }),
    onSuccess: () => showSuccessToast("Item access revoked"),
    onError: handleError.bind(showErrorToast),
    onSettled: invalidateQueries,
  })

  const isPending = shareMutation.isPending || revokeMutation.isPending

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        setIsOpen(open)
        if (!open) {
          form.reset()
          onClose()
        }
      }}
    >
      <DropdownMenuItem
        onSelect={(event) => event.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        <Share2 />
        Share Item
      </DropdownMenuItem>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Share Item</DialogTitle>
          <DialogDescription>
            Share “{item.title}” by email. People you share with can view and
            edit this item, but cannot delete it or share it with others.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit((data) => shareMutation.mutate(data))}
            className="flex items-start gap-3"
          >
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem className="flex-1 min-w-0">
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      placeholder="name@example.com"
                      autoComplete="email"
                      required
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <LoadingButton
              type="submit"
              className="mt-6"
              loading={shareMutation.isPending}
              disabled={isPending}
            >
              Share
            </LoadingButton>
          </form>
        </Form>
        <section aria-label="People with access" className="grid gap-3">
          <h3 className="text-sm font-medium">People with access</h3>
          {shares.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">
              Loading people with access…
            </p>
          ) : shares.isError ? (
            <div role="alert" className="text-sm text-destructive">
              Unable to load people with access.
              <Button variant="link" onClick={() => shares.refetch()}>
                Retry
              </Button>
            </div>
          ) : shares.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This item hasn't been shared with anyone yet.
            </p>
          ) : (
            <ul className="grid gap-3 max-h-64 overflow-y-auto">
              {shares.data.map((share) => (
                <li key={share.id} className="flex items-center gap-3">
                  <div className="min-w-0 flex-1 text-sm">
                    {share.full_name && (
                      <p className="font-medium truncate">{share.full_name}</p>
                    )}
                    <p className="text-muted-foreground break-all">
                      {share.email}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {share.permission === "edit" ? "Can edit" : "Can view"}
                    </p>
                  </div>
                  <LoadingButton
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Unshare with ${share.email}`}
                    loading={
                      revokeMutation.isPending &&
                      revokeMutation.variables === share.id
                    }
                    disabled={isPending}
                    onClick={() => revokeMutation.mutate(share.id)}
                  >
                    Unshare
                  </LoadingButton>
                </li>
              ))}
            </ul>
          )}
        </section>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              Close
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default ShareItem
