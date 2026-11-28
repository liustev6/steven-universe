import { redirect } from "next/navigation";

/** /prototype opens the prototype's home page. */
export default function PrototypeIndex() {
  redirect("/prototype/home");
}
