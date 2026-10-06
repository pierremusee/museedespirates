"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { format } from "date-fns";
import { fr } from "date-fns/locale";
import { CalendarIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

/** Sélecteur de jour du catalogue : pousse ?date=YYYY-MM-DD dans l'URL,
 *  la page serveur refetch /events?date=... automatiquement. */
export function DatePicker({ value }: { value: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const selected = new Date(`${value}T12:00:00`);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" className="justify-start gap-2">
          <CalendarIcon className="h-4 w-4" />
          {format(selected, "EEEE d MMMM yyyy", { locale: fr })}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          locale={fr}
          selected={selected}
          defaultMonth={selected}
          onSelect={(day) => {
            setOpen(false);
            if (day) {
              router.push(`/reserver?date=${format(day, "yyyy-MM-dd")}`);
            }
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
