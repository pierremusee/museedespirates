import Link from "next/link";
import { Anchor, Ticket } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function Home() {
  return (
    <main className="flex flex-1 items-center justify-center bg-gradient-to-b from-background to-muted px-4">
      <Card className="w-full max-w-md border-2 shadow-xl">
        <CardHeader className="items-center text-center">
          <Anchor className="mb-2 size-10 text-primary" aria-hidden />
          <CardTitle className="text-3xl font-bold tracking-tight">
            Musée des Pirates
          </CardTitle>
          <CardDescription className="text-base">
            Embarquez pour une aventure au cœur de l'âge d'or de la piraterie :
            expositions, Théâtre du Kraken et Taverne vous attendent.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-center text-sm text-muted-foreground">
          Réservez vos billets en ligne et évitez la file d'attente à
          l'embarquement.
        </CardContent>
        <CardFooter className="justify-center">
          <Button asChild size="lg" className="gap-2">
            <Link href="/reserver">
              <Ticket className="size-4" aria-hidden />
              Réserver mes billets
            </Link>
          </Button>
        </CardFooter>
      </Card>
    </main>
  );
}
