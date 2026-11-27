/* os/x2000-c-build.py: ncurses, form, menu and panel output on a pipe.
 * Draws windows, panels, a menu and a form on a terminal of type argv[1],
 * then writes the screen's cells; the escape sequences go to stdout. */
#include <curses.h>
#include <form.h>
#include <menu.h>
#include <panel.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
	SCREEN *screen = newterm(argc > 1 ? argv[1] : "vt100", stdout, stdin);
	if (!screen)
		return 2;
	set_term(screen);
	start_color();
	init_pair(1, COLOR_RED, COLOR_BLACK);
	WINDOW *a = newwin(10, 30, 1, 1), *b = newwin(8, 40, 5, 20);
	box(a, 0, 0);
	box(b, ACS_VLINE, ACS_HLINE);
	for (int i = 0; i < 8; i++)
		mvwprintw(a, i + 1, 1, "%d line %08x %s", i, i * 2654435761u, i & 1 ? "odd" : "even");
	wattron(b, COLOR_PAIR(1) | A_BOLD);
	mvwaddstr(b, 1, 2, "panel b over panel a");
	PANEL *pa = new_panel(a), *pb = new_panel(b);
	update_panels();
	doupdate();
	top_panel(pa);
	hide_panel(pb);
	show_panel(pb);
	update_panels();
	doupdate();

	ITEM *items[6];
	const char *names[] = {"Artists", "Albums", "Songs", "Podcasts", "Settings"};
	for (int i = 0; i < 5; i++)
		items[i] = new_item(names[i], "");
	items[5] = NULL;
	MENU *menu = new_menu(items);
	WINDOW *m = newwin(7, 20, 12, 2);
	set_menu_win(menu, m);
	set_menu_sub(menu, derwin(m, 5, 18, 1, 1));
	post_menu(menu);
	menu_driver(menu, REQ_DOWN_ITEM);
	menu_driver(menu, REQ_DOWN_ITEM);
	wrefresh(m);

	FIELD *fields[3] = {new_field(1, 16, 0, 0, 0, 0), new_field(1, 16, 2, 0, 0, 0), NULL};
	set_field_buffer(fields[0], 0, "user q2");
	set_field_type(fields[1], TYPE_INTEGER, 0, 0, 99999);
	FORM *form = new_form(fields);
	WINDOW *f = newwin(4, 20, 12, 30);
	set_form_win(form, f);
	set_form_sub(form, derwin(f, 3, 18, 0, 1));
	post_form(form);
	form_driver(form, REQ_NEXT_FIELD);
	for (const char *p = "4533"; *p; p++)
		form_driver(form, *p);
	form_driver(form, REQ_VALIDATION);
	wrefresh(f);
	endwin();

	printf("\n--- cells\n");
	for (int y = 0; y < LINES; y++) {
		for (int x = 0; x < COLS; x++)
			printf("%lx,", (unsigned long)mvwinch(curscr, y, x));
		printf("\n");
	}
	printf("item %s field [%s] [%s]\n", item_name(current_item(menu)), field_buffer(fields[0], 0), field_buffer(fields[1], 0));
	return 0;
}
