import { updateMyOnboardingCategories, type OnboardingCategory } from "./profile-graphql.ts";
import { loadPreferences, savePreferences } from "./settings-store.ts";
import type { Result } from "./auth-store.ts";

export async function saveMyOnboardingCategories(
  email: string,
  categoryNames: string[],
): Promise<Result<OnboardingCategory[]>> {
  const result = await updateMyOnboardingCategories(
    categoryNames.map((name) => name.toLowerCase()),
  );
  if (!result.ok) return result;

  const preferences = loadPreferences(email);
  savePreferences(email, {
    ...preferences,
    categories: result.value.map((category) => category.name),
  });
  return result;
}
