import { expect, type Page, type TestInfo, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import {
  randomEmail,
  randomItemDescription,
  randomItemTitle,
  randomPassword,
} from "./utils/random"
import { logInUser } from "./utils/user"

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`)
  await page.screenshot({ path, fullPage: true })
  await testInfo.attach(name, { path, contentType: "image/png" })
}

test("shared items beyond the first API page are reachable", async ({
  page,
}) => {
  const items = Array.from({ length: 101 }, (_, index) => ({
    id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
    owner_id: "00000000-0000-0000-0001-000000000000",
    title: `Shared boundary item ${index + 1}`,
    description: null,
    created_at: "2026-01-01T00:00:00Z",
  }))
  await page.route("**/api/v1/items/shared-with-me?*", async (route) => {
    const query = new URL(route.request().url()).searchParams
    const skip = Number(query.get("skip") ?? 0)
    const limit = Number(query.get("limit") ?? 100)
    await route.fulfill({
      json: { data: items.slice(skip, skip + limit), count: items.length },
    })
  })
  await page.goto("/items")
  await page.getByRole("tab", { name: "Shared with me" }).click()
  await expect(
    page.getByText("Shared boundary item 1", { exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Go to last page" }).click()
  await expect(
    page.getByText("Shared boundary item 101", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Go to next page" }),
  ).toBeDisabled()
})

test("owners share and revoke access while recipients can only edit", async ({
  browser,
  baseURL,
}, testInfo) => {
  test.setTimeout(60_000)
  const ownerEmail = randomEmail()
  const recipientEmail = randomEmail()
  const ownerPassword = randomPassword()
  const recipientPassword = randomPassword()
  const [owner, recipient] = await Promise.all([
    createUser({ email: ownerEmail, password: ownerPassword }),
    createUser({ email: recipientEmail, password: recipientPassword }),
  ])
  // These must be normal users: a superuser can bypass item permissions.
  expect(owner.is_superuser).toBe(false)
  expect(recipient.is_superuser).toBe(false)

  const ownerContext = await browser.newContext({
    baseURL,
    storageState: { cookies: [], origins: [] },
  })
  const recipientContext = await browser.newContext({
    baseURL,
    storageState: { cookies: [], origins: [] },
  })
  try {
    const ownerPage = await ownerContext.newPage()
    const recipientPage = await recipientContext.newPage()
    const title = randomItemTitle()
    const editedTitle = randomItemTitle()
    const description = randomItemDescription()

    await test.step("sign in as two normal users in isolated contexts", async () => {
      await logInUser(ownerPage, ownerEmail, ownerPassword)
      await logInUser(recipientPage, recipientEmail, recipientPassword)
      await ownerPage.goto("/items")
      await recipientPage.goto("/items")
      await expect(
        recipientPage.getByText("You don't have any items yet"),
      ).toBeVisible()
    })

    await test.step("owner creates an item and shares it by email", async () => {
      await ownerPage.getByRole("button", { name: "Add Item" }).click()
      await ownerPage.getByLabel("Title").fill(title)
      await ownerPage.getByLabel("Description").fill(description)
      await ownerPage.getByRole("button", { name: "Save" }).click()
      await expect(ownerPage.getByRole("dialog")).not.toBeVisible()

      const row = ownerPage.getByRole("row").filter({ hasText: title })
      await row.getByRole("button", { name: "Item actions" }).click()
      await ownerPage.getByRole("menuitem", { name: "Share Item" }).click()
      const dialog = ownerPage.getByRole("dialog", { name: "Share Item" })
      await expect(
        dialog.getByText("This item hasn't been shared with anyone yet."),
      ).toBeVisible()
      await dialog.getByLabel("Email").fill(recipientEmail)
      await dialog.getByRole("button", { name: "Share", exact: true }).click()
      await expect(
        dialog.getByText(recipientEmail, { exact: true }),
      ).toBeVisible()
      await expect(dialog.getByText("Can edit", { exact: true })).toBeVisible()
      await screenshot(ownerPage, testInfo, "owner-share-dialog-with-recipient")

      // Sharing the same email twice must leave a single sharee in the list.
      await dialog.getByLabel("Email").fill(recipientEmail)
      await dialog.getByRole("button", { name: "Share", exact: true }).click()
      await expect(dialog.getByLabel("Email")).toHaveValue("")
      await expect(dialog.getByRole("listitem")).toHaveCount(1)
      await dialog
        .locator('[data-slot="dialog-footer"]')
        .getByRole("button", { name: "Close", exact: true })
        .click()
      await ownerPage.getByRole("tab", { name: "Shared with me" }).click()
      await expect(
        ownerPage.getByText("No items have been shared with you yet"),
      ).toBeVisible()
    })

    await test.step("recipient sees only edit actions and edits the shared item", async () => {
      await recipientPage.reload()
      await expect(
        recipientPage.getByText(title, { exact: true }),
      ).not.toBeVisible()
      await recipientPage.getByRole("tab", { name: "Shared with me" }).click()
      const row = recipientPage.getByRole("row").filter({ hasText: title })
      await expect(row).toBeVisible()
      await expect(row.getByText(description, { exact: true })).toBeVisible()
      await row.getByRole("button", { name: "Item actions" }).click()
      await expect(recipientPage.getByRole("menuitem")).toHaveCount(1)
      await expect(
        recipientPage.getByRole("menuitem", { name: "Edit Item" }),
      ).toBeVisible()
      await expect(
        recipientPage.getByRole("menuitem", { name: "Delete Item" }),
      ).toHaveCount(0)
      await expect(
        recipientPage.getByRole("menuitem", { name: "Share Item" }),
      ).toHaveCount(0)
      await screenshot(
        recipientPage,
        testInfo,
        "recipient-shared-items-edit-only",
      )

      await recipientPage.getByRole("menuitem", { name: "Edit Item" }).click()
      const dialog = recipientPage.getByRole("dialog", { name: "Edit Item" })
      await dialog.getByLabel("Title").fill(editedTitle)
      await screenshot(recipientPage, testInfo, "recipient-edit-shared-item")
      await dialog.getByRole("button", { name: "Save" }).click()
      await expect(dialog).not.toBeVisible()
      await expect(
        recipientPage.getByRole("row").filter({ hasText: editedTitle }),
      ).toBeVisible()
      await expect(
        recipientPage.getByText(title, { exact: true }),
      ).not.toBeVisible()
      await screenshot(
        recipientPage,
        testInfo,
        "recipient-shared-items-after-edit",
      )
    })

    await test.step("owner sees the edit, lists sharees, and revokes access", async () => {
      await ownerPage.reload()
      const row = ownerPage.getByRole("row").filter({ hasText: editedTitle })
      await expect(row).toBeVisible()
      await row.getByRole("button", { name: "Item actions" }).click()
      await ownerPage.getByRole("menuitem", { name: "Share Item" }).click()
      const dialog = ownerPage.getByRole("dialog", { name: "Share Item" })
      await expect(
        dialog.getByText(recipientEmail, { exact: true }),
      ).toBeVisible()
      await dialog
        .getByRole("button", { name: `Unshare with ${recipientEmail}` })
        .click()
      await expect(
        dialog.getByText(recipientEmail, { exact: true }),
      ).not.toBeVisible()
      await expect(
        dialog.getByText("This item hasn't been shared with anyone yet."),
      ).toBeVisible()
      await screenshot(ownerPage, testInfo, "owner-revoked-sharing")
      await dialog
        .locator('[data-slot="dialog-footer"]')
        .getByRole("button", { name: "Close", exact: true })
        .click()
    })

    await test.step("recipient loses the item after revocation", async () => {
      await recipientPage.reload()
      await recipientPage.getByRole("tab", { name: "Shared with me" }).click()
      await expect(
        recipientPage.getByText("No items have been shared with you yet"),
      ).toBeVisible()
      await expect(
        recipientPage.getByText(editedTitle, { exact: true }),
      ).not.toBeVisible()
      await screenshot(recipientPage, testInfo, "recipient-access-revoked")
    })

    await test.step("owner can still delete the item", async () => {
      const row = ownerPage.getByRole("row").filter({ hasText: editedTitle })
      await row.getByRole("button", { name: "Item actions" }).click()
      await ownerPage.getByRole("menuitem", { name: "Delete Item" }).click()
      await ownerPage
        .getByRole("button", { name: "Delete", exact: true })
        .click()
      await expect(ownerPage.getByRole("dialog")).not.toBeVisible()
      await expect(row).not.toBeVisible()
    })
  } finally {
    await ownerContext.close()
    await recipientContext.close()
  }
})
